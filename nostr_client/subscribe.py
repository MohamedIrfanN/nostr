import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

import websockets

from .config import RELAYS, READ_SINCE_SECONDS, READ_LIMIT, AUTHOR_CHUNK_SIZE


# Track events we've already seen (dedupe across relays)
seen_ids: set[str] = set()


def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def format_time(ts: int | None) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(int(ts)).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


async def read_from_relay(relay: str, authors: list[str], blocked_set: set[str] | None = None):
    sub_id = str(uuid.uuid4())
    req = [
        "REQ",
        sub_id,
        {
            "authors": authors,
            "kinds": [1],
            "since": int(time.time()) - READ_SINCE_SECONDS,
            "limit": READ_LIMIT,
        },
    ]

    try:
        async with websockets.connect(relay) as ws:
            await ws.send(json.dumps(req, separators=(",", ":")))

            while True:
                raw = await ws.recv()
                msg = json.loads(raw)

                if not msg:
                    continue

                if msg[0] != "EVENT":
                    continue

                _, got_sub_id, event = msg
                if got_sub_id != sub_id:
                    continue

                eid = event.get("id")
                if not eid or eid in seen_ids:
                    continue
                seen_ids.add(eid)

                created_at = event.get("created_at")
                content = (event.get("content") or "").replace("\n", " ").strip()
                author = (event.get("pubkey") or "")[:12]

                if author in blocked_set:
                    continue 
                

                print("\n🟦 EVENT")
                print(f"  relay: {relay}")
                print(f"  id:    {eid}")
                print(f"  time:  {format_time(created_at)}")
                print(f"  from:  {author}…")
                print(f"  text:  {content[:200]}")

    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"\n❌ Read error: {relay}")
        print(f"   {type(e).__name__}: {e}")


async def read_from_all_relays_following(authors: list[str], blocked_set: set[str] | None = None):
    """
    Subscribe to kind:1 events from followed authors across all relays.
    Authors are chunked to avoid relay filter limits.
    """
    if not authors:
        print("⚠️  No authors to subscribe to.")
        return

    # Clear per run so refresh/menu re-entry works
    seen_ids.clear()

    tasks = []
    for relay in RELAYS:
        for author_chunk in chunk(authors, AUTHOR_CHUNK_SIZE):
            tasks.append(
                asyncio.create_task(read_from_relay(relay, author_chunk, blocked_set))
            )

    print("\n==============================")
    print("📡 Reading events from following (Ctrl+C to return)")
    print("==============================")

    await asyncio.gather(*tasks)
