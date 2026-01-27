import asyncio
import json
import time
import uuid
import websockets

from .config import RELAYS, PING_INTERVAL, PING_TIMEOUT


# Simple in-memory cache: pubkey_hex -> display string
_name_cache: dict[str, str] = {}


def _short(pk: str) -> str:
    pk = (pk or "").lower()
    return pk[:12] + "…" if len(pk) >= 12 else pk


def _pick_name(meta: dict, pubkey: str) -> str:
    """
    Preferred order (common client behavior):
      display_name > name > fallback(pubkey short)
    """
    display_name = meta.get("display_name")
    name = meta.get("name")

    if isinstance(display_name, str) and display_name.strip():
        return display_name.strip()
    if isinstance(name, str) and name.strip():
        return name.strip()
    return _short(pubkey)


def _parse_kind0_content(content: str) -> dict:
    """
    kind:0 content is JSON string. Sometimes malformed.
    """
    try:
        obj = json.loads(content)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


async def _fetch_kind0_from_relay(relay: str, pubkeys: list[str], timeout_sec: int = 6) -> dict[str, dict]:
    """
    Fetch latest kind:0 per pubkey from ONE relay.
    Returns: { pubkey_hex -> parsed_metadata_dict }
    """
    if not pubkeys:
        return {}

    sub_id = str(uuid.uuid4())
    req = ["REQ", sub_id, {"kinds": [0], "authors": pubkeys, "limit": len(pubkeys)}]

    latest: dict[str, dict] = {}  # pubkey -> {"created_at": int, "meta": dict}

    try:
        async with websockets.connect(
            relay,
            ping_interval=PING_INTERVAL,
            ping_timeout=PING_TIMEOUT,
        ) as ws:
            await ws.send(json.dumps(req, separators=(",", ":")))

            start = time.time()
            while True:
                # hard stop
                if time.time() - start > timeout_sec:
                    break

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout_sec)
                except asyncio.TimeoutError:
                    break

                msg = json.loads(raw)
                if not msg:
                    continue

                if msg[0] == "EVENT":
                    _, got_sub, ev = msg
                    if got_sub != sub_id:
                        continue

                    pk = (ev.get("pubkey") or "").lower()
                    created_at = int(ev.get("created_at") or 0)
                    content = ev.get("content") or ""
                    meta = _parse_kind0_content(content)

                    cur = latest.get(pk)
                    if cur is None or created_at > cur["created_at"]:
                        latest[pk] = {"created_at": created_at, "meta": meta}

                elif msg[0] == "EOSE":
                    _, got_sub = msg
                    if got_sub == sub_id:
                        break

    except Exception:
        return {}

    return {pk: v["meta"] for pk, v in latest.items()}


def _chunk(lst: list[str], size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


async def get_display_names(pubkeys: list[str], chunk_size: int = 200) -> dict[str, str]:
    """
    Returns {pubkey_hex -> display_name_string}.
    Uses cache + best-effort relay fetch.
    """
    # normalize input
    pubkeys = [(pk or "").strip().lower() for pk in pubkeys]
    pubkeys = [pk for pk in pubkeys if len(pk) == 64]

    # quick return from cache
    result: dict[str, str] = {}
    missing: list[str] = []
    for pk in pubkeys:
        if pk in _name_cache:
            result[pk] = _name_cache[pk]
        else:
            missing.append(pk)

    if not missing:
        return result

    # fetch missing from relays (best effort)
    # We’ll accept the first good name we find per pubkey.
    for pk_chunk in _chunk(missing, chunk_size):
        relay_results = await asyncio.gather(*(_fetch_kind0_from_relay(r, pk_chunk) for r in RELAYS))

        # merge: first non-empty name wins (you can change to newest-across-relays later)
        merged_meta: dict[str, dict] = {}
        for d in relay_results:
            for pk, meta in (d or {}).items():
                if pk not in merged_meta:
                    merged_meta[pk] = meta

        for pk in pk_chunk:
            meta = merged_meta.get(pk, {})
            disp = _pick_name(meta, pk)
            _name_cache[pk] = disp
            result[pk] = disp

    return result
