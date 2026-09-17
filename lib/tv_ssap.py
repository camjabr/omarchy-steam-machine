#!/usr/bin/env python3
"""Talk to an LG webOS TV over SSAP.

Commands (argv[1]):
    state   print the power state word (Active, Active Standby, ...)
    off     send system/turnOff
    on      send system/turnOn (panel on; WoL is still LG Buddy's job)

Reuses the client key LG Buddy already paired. Exits non-zero on failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
from pathlib import Path

CONFIG = Path(
    os.environ.get("LG_BUDDY_CONFIG", Path.home() / ".config/lg-buddy/config.env")
)
TOKEN = Path(
    os.environ.get(
        "LG_BUDDY_TOKEN", Path.home() / ".config/lg-buddy/tvs/primary/access-token.json"
    )
)
TIMEOUT = int(os.environ.get("TV_POWER_STATE_TIMEOUT", "8"))


def tv_ip() -> str:
    if ip := os.environ.get("TV_IP"):
        return ip
    for line in CONFIG.read_text().splitlines():
        if line.startswith("tvs_primary_ip="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"no tvs_primary_ip in {CONFIG}")


def client_key() -> str:
    return json.loads(TOKEN.read_text())["access_token"]


class TvSession:
    def __init__(self, ip: str, key: str) -> None:
        self.ip = ip
        self.key = key
        self._ws = None
        self._next_id = 0

    async def __aenter__(self):
        from websockets.asyncio.client import connect

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        self._ws = await connect(
            f"wss://{self.ip}:3001/", ssl=context, open_timeout=TIMEOUT
        )
        await self._ws.send(
            json.dumps(
                {
                    "type": "register",
                    "id": "register_0",
                    "payload": {"forcePairing": False, "client-key": self.key},
                }
            )
        )
        while True:
            reply = json.loads(await self._ws.recv())
            if reply.get("type") == "registered":
                return self
            if reply.get("type") == "error":
                raise RuntimeError(reply.get("error", "register rejected"))

    async def __aexit__(self, *exc):
        if self._ws is not None:
            await self._ws.close()

    async def request(self, uri: str, payload: dict | None = None) -> dict:
        assert self._ws is not None
        self._next_id += 1
        rid = f"req_{self._next_id}"
        message = {"type": "request", "id": rid, "uri": uri}
        if payload is not None:
            message["payload"] = payload
        await self._ws.send(json.dumps(message))
        while True:
            reply = json.loads(await self._ws.recv())
            if reply.get("id") != rid:
                continue
            if reply.get("type") == "error":
                raise RuntimeError(reply.get("error", f"{uri} failed"))
            return reply.get("payload") or {}

    async def power_state(self) -> str:
        payload = await self.request(
            "ssap://com.webos.service.tvpower/power/getPowerState"
        )
        state = payload.get("state")
        if not state:
            raise RuntimeError(f"no state in reply: {payload}")
        processing = payload.get("processing")
        return f"{state}|{processing}" if processing else state

    async def turn_off(self) -> None:
        await self.request("ssap://system/turnOff")

    async def turn_on(self) -> None:
        await self.request("ssap://system/turnOn")


async def run(command: str) -> str | None:
    async with TvSession(tv_ip(), client_key()) as tv:
        if command == "state":
            return await tv.power_state()
        if command == "off":
            await tv.turn_off()
            return None
        if command == "on":
            await tv.turn_on()
            return None
        raise RuntimeError(f"unknown command: {command}")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in {"state", "off", "on"}:
        print(f"Usage: {Path(sys.argv[0]).name} {{state|off|on}}", file=sys.stderr)
        return 2
    command = sys.argv[1]
    try:
        result = asyncio.run(asyncio.wait_for(run(command), timeout=TIMEOUT + 4))
    except Exception as exc:  # noqa: BLE001
        print(f"tv-power: {exc}", file=sys.stderr)
        return 1
    if result is not None:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
