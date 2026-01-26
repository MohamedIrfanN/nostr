import asyncio
import json
import time
import uuid
import websockets
from .utils import normalize_pubkey_input

from .config import RELAYS, PING_INTERVAL, PING_TIMEOUT, CONTACTS_FETCH_TIMEOUT, CONTACTS_KIND


def parse_following_from_kind3(event: dict | None) -> set[str]:
    follows: set[str] = set()
    if not event:
        return follows

    for t in event.get("tags", []) or []:
        if isinstance(t, list) and len(t) >= 2 and t[0] == "p":
            pk = t[1]
            if isinstance(pk, str) and len(pk) == 64:
                follows.add(pk.lower())
    return follows


async def fetch_latest_contacts_event_from_relay(relay_url: str, my_pubkey: str) -> dict | None:
    """
    Fetch latest kind:3 contacts event (not just the pubkeys) from one relay.
    """
    sub_id = str(uuid.uuid4())
    req = ["REQ", sub_id, {"authors": [my_pubkey], "kinds": [CONTACTS_KIND], "limit": 1}]
    latest_event: dict | None = None

    try:
        async with websockets.connect(
            relay_url,
            ping_interval=PING_INTERVAL,
            ping_timeout=PING_TIMEOUT,
        ) as ws:
            await ws.send(json.dumps(req, separators=(",", ":")))

            start = time.time()
            while True:
                if time.time() - start > CONTACTS_FETCH_TIMEOUT:
                    break

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=CONTACTS_FETCH_TIMEOUT)
                except asyncio.TimeoutError:
                    break

                msg = json.loads(raw)
                if not msg:
                    continue

                if msg[0] == "EVENT":
                    _, got_sub, ev = msg
                    if got_sub != sub_id:
                        continue
                    if latest_event is None or ev.get("created_at", 0) > latest_event.get("created_at", 0):
                        latest_event = ev

                elif msg[0] == "EOSE":
                    _, got_sub = msg
                    if got_sub == sub_id:
                        break

    except Exception as e:
        print(f"⚠️  contacts fetch failed {relay_url}: {type(e).__name__}: {e}")
        return None

    return latest_event


async def fetch_latest_contacts_event_all_relays(my_pubkey: str) -> dict | None:
    """
    Try all relays; return the newest contacts event we find.
    """
    events = await asyncio.gather(*(fetch_latest_contacts_event_from_relay(r, my_pubkey) for r in RELAYS))
    newest = None
    for ev in events:
        if not ev:
            continue
        if newest is None or ev.get("created_at", 0) > newest.get("created_at", 0):
            newest = ev
    return newest


async def fetch_following_all_relays(my_pubkey: str) -> set[str]:
    latest = await fetch_latest_contacts_event_all_relays(my_pubkey)
    return parse_following_from_kind3(latest)


def apply_follow(current: set[str], pubkey_input: str) -> set[str]:
    pk = normalize_pubkey_input(pubkey_input)
    return set(current) | {pk}


def apply_unfollow(current: set[str], pubkey_input: str) -> set[str]:
    pk = normalize_pubkey_input(pubkey_input)
    nxt = set(current)
    nxt.discard(pk)
    return nxt

