import asyncio
import json
import websockets
from .config import RELAYS, PING_INTERVAL, PING_TIMEOUT


async def publish_to_relay(relay_url: str, event_id: str, event: dict, quiet: bool = False) -> bool:
    try:
        async with websockets.connect(
            relay_url,
            ping_interval=PING_INTERVAL,
            ping_timeout=PING_TIMEOUT,
        ) as ws:
            await ws.send(json.dumps(["EVENT", event]))
            while True:
                msg = json.loads(await ws.recv())
                if msg[0] == "OK" and msg[1] == event_id:
                    if not quiet:
                        print(f"{relay_url}: {msg[2]} {msg[3]}")
                    return bool(msg[2])
    except Exception as e:
        if not quiet:
            print(f"{relay_url}: publish error {e}")
        return False


async def publish_to_relays(event_id: str, event: dict, quiet: bool = False):
    results = await asyncio.gather(
        *(publish_to_relay(r, event_id, event, quiet=quiet) for r in RELAYS)
    )
    if not quiet:
        print(f"Published to {sum(results)}/{len(RELAYS)} relays")
