import asyncio
import json
import uuid
import websockets

from .config import RELAYS, PING_INTERVAL, PING_TIMEOUT


async def fetch_event_by_id_from_relay(relay_url: str, event_id: str, timeout_sec: int = 5) -> dict | None:
    sub_id = str(uuid.uuid4())
    req = ["REQ", sub_id, {"ids": [event_id], "limit": 1}]

    try:
        async with websockets.connect(
            relay_url,
            ping_interval=PING_INTERVAL,
            ping_timeout=PING_TIMEOUT,
        ) as ws:
            await ws.send(json.dumps(req, separators=(",", ":")))

            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout_sec)
                msg = json.loads(raw)

                if not msg:
                    continue

                if msg[0] == "EVENT":
                    _, got_sub, ev = msg
                    if got_sub == sub_id:
                        return ev

                if msg[0] == "EOSE":
                    _, got_sub = msg
                    if got_sub == sub_id:
                        return None

    except Exception:
        return None


async def fetch_event_by_id_all_relays(event_id: str) -> dict | None:
    results = await asyncio.gather(*(fetch_event_by_id_from_relay(r, event_id) for r in RELAYS))
    for ev in results:
        if ev:
            return ev
    return None
