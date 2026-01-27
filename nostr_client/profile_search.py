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
    since_days: int = 30,
    per_relay_limit: int = 150,   # was 400 (too slow for fallback)
) -> list[dict]:
    q = (name_query or "").strip().lower()
    if not q:
        return []

    since = int(time.time()) - since_days * 24 * 60 * 60
    found: dict[str, dict] = {}

    RECV_TIMEOUT = 3.0  # seconds per recv; prevents "stuck"

    async def _try_nip50(relay: str):
        sub_id = str(uuid.uuid4())
        req = ["REQ", sub_id, {"kinds": [0], "search": q, "since": since, "limit": 20}]
        try:
            async with websockets.connect(relay, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps(req, separators=(",", ":")))
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
                    except asyncio.TimeoutError:
                        break  # relay too slow / no EOSE
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
                    prof["_pubkey"] = pk
                    prof["_created_at"] = int(ev.get("created_at", 0))

                    cur = found.get(pk)
                    if cur is None or prof["_created_at"] > cur["_created_at"]:
                        found[pk] = prof
        except Exception:
            return

    async def _fallback_scan(relay: str):
        sub_id = str(uuid.uuid4())
        req = ["REQ", sub_id, {"kinds": [0], "since": since, "limit": per_relay_limit}]
        try:
            async with websockets.connect(relay, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps(req, separators=(",", ":")))
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
                    except asyncio.TimeoutError:
                        break
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
                    name = str(prof.get("name", "")).lower()
                    dname = str(prof.get("display_name", "")).lower()
                    nip05 = str(prof.get("nip05", "")).lower()
                    if q not in name and q not in dname and q not in nip05:
                        continue

                    prof["_pubkey"] = pk
                    prof["_created_at"] = int(ev.get("created_at", 0))

                    cur = found.get(pk)
                    if cur is None or prof["_created_at"] > cur["_created_at"]:
                        found[pk] = prof
        except Exception:
            return

    # 1) Try NIP-50
    await asyncio.gather(*(_try_nip50(r) for r in RELAYS))

    # # 2) Fallback scan only if nothing found
    if not found:
        await asyncio.gather(*(_fallback_scan(r) for r in RELAYS))

    out = list(found.values())
    out.sort(key=lambda p: int(p.get("_created_at", 0)), reverse=True)
    return out

