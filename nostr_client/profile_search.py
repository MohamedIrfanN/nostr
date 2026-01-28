import asyncio
import json
import time
import uuid
import websockets

from .config import RELAYS
from .utils import normalize_pubkey_input


def _parse_kind0_content(ev: dict) -> dict:
    """
    kind:0 content is JSON string with fields like:
    name, display_name, about, picture, nip05, lud16...
    """
    raw = ev.get("content") or ""
    try:
        return json.loads(raw) if isinstance(raw, str) else {}
    except Exception:
        return {}


async def fetch_profile_by_pubkey(pubkey_input: str, timeout_sec: float = 3.0) -> dict | None:
    """
    Best-effort: ask all relays for kind:0 authored by pubkey.
    Returns a dict with profile fields + 'pubkey' if found, else None.
    """
    pubkey = normalize_pubkey_input(pubkey_input)
    since = int(time.time()) - 365 * 24 * 60 * 60  # last year, generous

    async def _from_relay(relay: str):
        sub_id = str(uuid.uuid4())
        req = ["REQ", sub_id, {"kinds": [0], "authors": [pubkey], "since": since, "limit": 5}]
        best = None
        try:
            async with websockets.connect(relay, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps(req, separators=(",", ":")))
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout_sec))
                    if msg[0] == "EOSE":
                        break
                    if msg[0] != "EVENT":
                        continue
                    ev = msg[2]
                    # pick latest
                    if best is None or int(ev.get("created_at", 0)) > int(best.get("created_at", 0)):
                        best = ev
        except Exception:
            return None
        return best

    results = await asyncio.gather(*(_from_relay(r) for r in RELAYS))
    events = [ev for ev in results if ev]
    if not events:
        return None

    best = max(events, key=lambda e: int(e.get("created_at", 0)))
    profile = _parse_kind0_content(best)
    profile["_pubkey"] = pubkey
    profile["_created_at"] = int(best.get("created_at", 0))
    return profile


async def search_profiles_by_name(
    name_query: str,
    limit: int = 10,
) -> list[dict]:
    raw_q = (name_query or "").strip()
    if not raw_q:
        return []

    # local compare uses lowercase
    q = raw_q.lower()

    found: dict[str, dict] = {}
    RECV_TIMEOUT = 3

    def _matches(prof: dict) -> bool:
        name = str(prof.get("name", "")).lower()
        dname = str(prof.get("display_name", "")).lower()
        nip05 = str(prof.get("nip05", "")).lower()
        about = str(prof.get("about", "")).lower()
        return (q in name) or (q in dname) or (q in nip05) or (q in about)

    async def _try_nip50(relay: str):
        sub_id = str(uuid.uuid4())
        # IMPORTANT: send the original query (not lowercased) to the relay
        req = ["REQ", sub_id, {"kinds": [0], "search": raw_q, "limit": 50}]

        try:
            async with websockets.connect(relay, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps(req, separators=(",", ":")))

                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
                    except asyncio.TimeoutError:
                        break  # relay slow / no EOSE

                    msg = json.loads(raw)

                    if msg[0] == "EOSE":
                        break
                    if msg[0] != "EVENT":
                        continue

                    ev = msg[2]
                    pk = ev.get("pubkey")
                    if not pk:
                        continue

                    prof = _parse_kind0_content(ev)
                    if not _matches(prof):
                        continue

                    prof["_pubkey"] = pk
                    prof["_created_at"] = int(ev.get("created_at", 0))

                    cur = found.get(pk)
                    if cur is None or prof["_created_at"] > cur["_created_at"]:
                        found[pk] = prof

        except Exception:
            return

    await asyncio.gather(*(_try_nip50(r) for r in RELAYS))

    out = list(found.values())

    # relevance like your friend (exact > startswith > contains)
    def relevance_score(p: dict) -> int:
        name = str(p.get("name", "")).lower()
        dname = str(p.get("display_name", "")).lower()
        nip05 = str(p.get("nip05", "")).lower()

        if name == q or dname == q or nip05 == q:
            return 0
        if name.startswith(q) or dname.startswith(q) or nip05.startswith(q):
            return 1
        return 2

    out.sort(key=lambda p: (relevance_score(p), -int(p.get("_created_at", 0))))
    return out[:limit]



