#!/usr/bin/env python3
"""Print the LG TV's real power state, one word on stdout.

Reachability is not power. With Quick Start+ enabled the TV answers on its
webOS port while the panel is off, and it keeps answering for several seconds
after being told to turn off, so a TCP probe reports a sleeping TV as
available. Only com.webos.service.tvpower distinguishes them:

    Active          panel on
    Active Standby  awake enough to answer, panel off
    Suspend         asleep
    Screen Off      on, panel blanked

Transitional states come back as "Active" with a processing field, which is
reported as "Active|<processing>" so callers can match "Active" exactly and
treat a TV that is still powering up or down as not ready.

Reuses the client key LG Buddy already paired, so this needs no new approval
on the TV. Exits non-zero when the state cannot be determined.
"""

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


async def power_state(ip: str, key: str) -> str:
    from websockets.asyncio.client import connect

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    async with connect(f"wss://{ip}:3001/", ssl=context, open_timeout=TIMEOUT) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "register",
                    "id": "register_0",
                    "payload": {"forcePairing": False, "client-key": key},
                }
            )
        )

        # The TV answers "registered" for a known key; anything else means the
        # key was rejected and there is no point waiting for a pairing prompt.
        while True:
            reply = json.loads(await ws.recv())
            if reply.get("type") == "registered":
                break
            if reply.get("type") == "error":
                raise RuntimeError(reply.get("error", "register rejected"))

        await ws.send(
            json.dumps(
                {
                    "type": "request",
                    "id": "power_1",
                    "uri": "ssap://com.webos.service.tvpower/power/getPowerState",
                }
            )
        )

        while True:
            reply = json.loads(await ws.recv())
            if reply.get("id") != "power_1":
                continue
            if reply.get("type") == "error":
                raise RuntimeError(reply.get("error", "getPowerState failed"))
            payload = reply.get("payload", {})
            state = payload.get("state")
            if not state:
                raise RuntimeError(f"no state in reply: {payload}")
            processing = payload.get("processing")
            return f"{state}|{processing}" if processing else state


def main() -> int:
    try:
        state = asyncio.run(
            asyncio.wait_for(power_state(tv_ip(), client_key()), timeout=TIMEOUT + 4)
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "unknown"
        print(f"tv-power-state: {exc}", file=sys.stderr)
        return 1
    print(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
