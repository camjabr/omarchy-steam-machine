#!/usr/bin/env python3
"""Power the TV off at the logind Suspend method_call — while Wi-Fi is still up.

A fresh SSAP connect at Suspend time loses to NetworkManager: routes are gone
within the same second (ENETUNREACH). This process keeps an authenticated
websocket open whenever console-mode is on the TV, so Suspend only has to
write turnOff on the live socket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import sys
import time
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

LOG = logging.getLogger("console-mode-tv-sleep")
MODE_GLOB = Path("/home").glob("*/.local/state/console-mode/mode")
PING_EVERY = 20.0
MODE_POLL = 2.0
RECONNECT_DELAY = 2.0


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )
    try:
        from logging.handlers import SysLogHandler

        handler = SysLogHandler(address="/dev/log")
        handler.setFormatter(logging.Formatter("console-mode-tv-sleep: %(message)s"))
        LOG.addHandler(handler)
    except Exception:  # noqa: BLE001
        pass


def tv_sessions() -> list[tuple[str, Path]]:
    sessions = []
    for mode_file in Path("/home").glob("*/.local/state/console-mode/mode"):
        try:
            if mode_file.read_text().strip() != "tv":
                continue
        except OSError:
            continue
        user = mode_file.parts[2]
        home = Path("/home") / user
        token = home / ".config/lg-buddy/tvs/primary/access-token.json"
        config = home / ".config/lg-buddy/config.env"
        if token.is_file() and config.is_file():
            sessions.append((user, home))
    return sessions


def tv_ip(home: Path) -> str:
    for line in (home / ".config/lg-buddy/config.env").read_text().splitlines():
        if line.startswith("tvs_primary_ip="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"no tvs_primary_ip in {home}")


def client_key(home: Path) -> str:
    return json.loads(
        (home / ".config/lg-buddy/tvs/primary/access-token.json").read_text()
    )["access_token"]


class LiveTv:
    def __init__(self, user: str, home: Path) -> None:
        self.user = user
        self.home = home
        self._ws = None
        self._next_id = 0
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def connect(self) -> None:
        ip = tv_ip(self.home)
        key = client_key(self.home)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ws = await connect(f"wss://{ip}:3001/", ssl=ctx, open_timeout=8)
        await ws.send(
            json.dumps(
                {
                    "type": "register",
                    "id": "register_0",
                    "payload": {"forcePairing": False, "client-key": key},
                }
            )
        )
        while True:
            reply = json.loads(await ws.recv())
            if reply.get("type") == "registered":
                break
            if reply.get("type") == "error":
                await ws.close()
                raise RuntimeError(reply.get("error", "register rejected"))
        self._ws = ws
        self._next_id = 0
        LOG.info("SSAP connected for %s (%s)", self.user, ip)

    async def close(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def turn_off(self) -> bool:
        async with self._lock:
            if self._ws is None:
                LOG.warning("no live SSAP for %s; trying cold connect", self.user)
                try:
                    await self.connect()
                except Exception as exc:  # noqa: BLE001
                    LOG.error("cold connect failed for %s: %s", self.user, exc)
                    return False
            assert self._ws is not None
            self._next_id += 1
            rid = f"off_{self._next_id}"
            try:
                await self._ws.send(
                    json.dumps(
                        {
                            "type": "request",
                            "id": rid,
                            "uri": "ssap://system/turnOff",
                        }
                    )
                )
                while True:
                    reply = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=3))
                    if reply.get("id") == rid:
                        LOG.info("turnOff reply for %s: %s", self.user, reply.get("type"))
                        return reply.get("type") != "error"
            except Exception as exc:  # noqa: BLE001
                LOG.error("turnOff on live socket failed for %s: %s", self.user, exc)
                await self.close()
                return False

    async def ping(self) -> None:
        if self._ws is None:
            return
        self._next_id += 1
        rid = f"ping_{self._next_id}"
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "type": "request",
                        "id": rid,
                        "uri": "ssap://com.webos.service.tvpower/power/getPowerState",
                    }
                )
            )
            while True:
                reply = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=5))
                if reply.get("id") == rid:
                    return
        except Exception as exc:  # noqa: BLE001
            LOG.warning("SSAP ping failed for %s: %s", self.user, exc)
            await self.close()


class Agent:
    def __init__(self) -> None:
        self._tvs: dict[str, LiveTv] = {}
        self._last_off = 0.0

    async def sync_sessions(self) -> None:
        wanted = {user: home for user, home in tv_sessions()}
        for user in list(self._tvs):
            if user not in wanted:
                LOG.info("leaving TV mode for %s; dropping SSAP", user)
                await self._tvs.pop(user).close()
        for user, home in wanted.items():
            tv = self._tvs.get(user)
            if tv is None:
                tv = LiveTv(user, home)
                self._tvs[user] = tv
            if not tv.connected:
                try:
                    await tv.connect()
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("SSAP connect for %s failed: %s", user, exc)

    async def maintain(self) -> None:
        last_ping = 0.0
        while True:
            await self.sync_sessions()
            now = time.monotonic()
            if self._tvs and now - last_ping >= PING_EVERY:
                for tv in list(self._tvs.values()):
                    if tv.connected:
                        await tv.ping()
                last_ping = now
            await asyncio.sleep(MODE_POLL)

    async def power_off_all(self) -> None:
        now = time.monotonic()
        if now - self._last_off < 5:
            LOG.info("power-off within cooldown, skipping")
            return
        self._last_off = now
        if not self._tvs:
            # Mode file might say tv but we have not connected yet.
            await self.sync_sessions()
        if not self._tvs:
            LOG.info("Suspend requested; no console-mode TV session")
            return
        for tv in list(self._tvs.values()):
            LOG.info("Suspend requested; powering off TV for %s", tv.user)
            ok = await tv.turn_off()
            if ok:
                LOG.info("TV powered off for %s", tv.user)
            else:
                LOG.error("TV power-off failed for %s", tv.user)
            await tv.close()

    async def watch_suspend(self) -> None:
        matches = [
            "type='method_call',path='/org/freedesktop/login1',"
            "interface='org.freedesktop.login1.Manager',member='Suspend'",
            "type='method_call',path='/org/freedesktop/login1',"
            "interface='org.freedesktop.login1.Manager',member='Hibernate'",
            "type='method_call',path='/org/freedesktop/login1',"
            "interface='org.freedesktop.login1.Manager',member='HybridSleep'",
            "type='method_call',path='/org/freedesktop/login1',"
            "interface='org.freedesktop.login1.Manager',member='SuspendThenHibernate'",
        ]
        while True:
            proc = await asyncio.create_subprocess_exec(
                "dbus-monitor",
                "--system",
                "--monitor",
                *matches,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            assert proc.stdout is not None
            LOG.info("watching logind Suspend method calls")
            try:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode(errors="replace").rstrip()
                    if not text.startswith("method call"):
                        continue
                    if "member=Suspend" in text or "member=Hibernate" in text or "member=HybridSleep" in text or "member=SuspendThenHibernate" in text:
                        await self.power_off_all()
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            LOG.warning("dbus-monitor exited; restarting in %ss", RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)


async def main() -> None:
    setup_logging()
    agent = Agent()
    await asyncio.gather(agent.maintain(), agent.watch_suspend())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
