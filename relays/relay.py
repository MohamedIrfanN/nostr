import asyncio
import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from secp256k1 import PublicKey  # provided by "secp256k1" package


app = FastAPI(title="Simple Nostr Relay (In-Memory)")



# -----------------------------
# In-memory state
# -----------------------------
EVENTS_BY_ID: Dict[str, Dict[str, Any]] = {} # event_id -> event
EVENTS_LIST: List[Dict[str, Any]] = []       # append-only list for simple scans

# Each connected websocket has connection
# subs[sub_id] = [filter_obj, filter_obj, ...]
CLIENT_SUBS: Dict[WebSocket, Dict[str, List[Dict[str, Any]]]] = {}


STATE_LOCK = asyncio.Lock()


def canonical_preimage(event: Dict[str, Any]) -> str:
    """ Return canonical preimage for event """
    data = [
        0,
        event["pubkey"],
        event["created_at"],
        event["kind"],
        event["tags"],
        event["content"],   
    ]
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def compute_event_id(event: Dict[str, Any]) -> str:
    """ Return event id for event """
    return hashlib.sha256(canonical_preimage(event).encode("utf-8")).hexdigest()

def is_hex_len(s: any, n: int) -> bool:
    """ Return True if s is a hex string of length n """
    if not isinstance(s, str) or len(s) != n:
        return False
    try:
        bytes.fromhex(s)
        return True
    except Exception:
        return False


def validate_event_shape(event: Dict[str, Any]) -> Optional[str]:
    """
    Basic structural validation (cheap checks).
    Return error string if invalid, else None.
    """
    if not isinstance(event, dict):
        return "event must be an object"

    required = ["id", "pubkey", "created_at", "kind", "tags", "content", "sig"]
    for k in required:
        if k not in event:
            return f"missing field: {k}"

    if not is_hex_len(event["id"], 64):
        return "invalid id (must be 32-byte hex / 64 chars)"
    if not is_hex_len(event["pubkey"], 64):
        return "invalid pubkey (must be 32-byte hex / 64 chars, x-only)"
    if not isinstance(event["created_at"], int):
        return "created_at must be int"
    if not isinstance(event["kind"], int):
        return "kind must be int"
    if not isinstance(event["tags"], list):
        return "tags must be list"
    if not isinstance(event["content"], str):
        return "content must be string"
    if not is_hex_len(event["sig"], 128):
        return "invalid sig (must be 64-byte hex / 128 chars)"

    return None

def verify_event(event: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Return (valid, error) tuple.
    """
    err = validate_event_shape(event)
    if err:
        return False, err

    recomputed_id = compute_event_id(event)
    if recomputed_id != event["id"]:
        return False, "id does not match computed id"
    
    try:
        pubkey_bytes = bytes.fromhex(event["pubkey"])
        sig_bytes = bytes.fromhex(event["sig"])
        msg = bytes.fromhex(event["id"])

        pubkey_xonly = bytes.fromhex(event["pubkey"])       # 32 bytes
        pubkey_compressed = b"\x02" + pubkey_xonly          # 33 bytes (even Y)
        pk = PublicKey(pubkey_compressed, raw=True)
        ok = pk.schnorr_verify(msg, sig_bytes, None, raw=True)
        if not ok:
            return False, "invalid signature"
    
    except Exception as e:
        return False, f"signature verify error: {type(e).__name__}: {e}"
    
    return True, "ok"

# -----------------------------
# Filters
# -----------------------------
def event_matches_filter(event: Dict[str, Any], filt: Dict[str, Any]) -> bool:
    """
    Supported fields:
      - kinds: [int]
      - authors: [hex pubkey]
      - since: int
      - until: int
    """

    if not isinstance(filt, dict):
        return False
    
    kinds = filt.get("kinds", None)
    if kinds is not None and event.get("kind") not in kinds:
        return False
    
    authors = filt.get("authors", None)
    if authors is not None and event.get("pubkey") not in authors:
        return False
    
    since = filt.get("since", None)
    if since is not None and event.get("created_at", 0) < since:
        return False
    
    until = filt.get("until", None)
    if until is not None and event.get("created_at", 0) > until:
        return False
    
    return True

def apply_filter_to_stored_events(filters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Return list of events that match any of the filters.
    """

    out_by_id: Dict[str, Dict[str, Any]] = {}

    stored = list(EVENTS_LIST)

    for flt in filters:
        matches = [e for e in stored if event_matches_filter(e, flt)]
        matches.sort(key=lambda e: e.get("created_at", 0), reverse=True)
        
        lim = flt.get("limit")

        if isinstance(lim, int) and lim > 0:
            matches = matches[:lim]
        
        for e in matches:
            out_by_id[e["id"]] = e
    

    merged = list(out_by_id.values())
    merged.sort(key=lambda e: e.get("created_at", 0), reverse=True)
    return merged

# -----------------------------
# WebSocket protocol handlers
# -----------------------------
async def send_notice(ws: WebSocket, msg: str):
    await ws.send_text(json.dumps(["NOTICE", msg], separators=(",", ":"), ensure_ascii=False))


async def handle_event_message(ws: WebSocket, event: Dict[str, Any]):
    ok, reason = verify_event(event)

    if not ok:
        # NIP-01: ["OK", <event_id>, false, "reason"]
        eid = event.get("id", "")
        await ws.send_text(json.dumps(["OK", eid, False, reason], separators=(",", ":"), ensure_ascii=False))
        return

    # Store (dedupe by id)
    async with STATE_LOCK:
        if event["id"] not in EVENTS_BY_ID:
            EVENTS_BY_ID[event["id"]] = event
            EVENTS_LIST.append(event)

    await ws.send_text(json.dumps(["OK", event["id"], True, "accepted"], separators=(",", ":"), ensure_ascii=False))

    # Live push: broadcast to matching subscriptions across clients
    await broadcast_event_to_subscribers(event)


async def handle_req_message(ws: WebSocket, sub_id: str, filters: List[Dict[str, Any]]):
    if not sub_id:
        await send_notice(ws, "REQ missing subscription id")
        return

    # Save subscription
    async with STATE_LOCK:
        CLIENT_SUBS.setdefault(ws, {})
        CLIENT_SUBS[ws][sub_id] = filters

    # Send stored matches, then EOSE
    async with STATE_LOCK:
        matches = apply_filter_to_stored_events(filters)

    for e in matches:
        await ws.send_text(json.dumps(["EVENT", sub_id, e], separators=(",", ":"), ensure_ascii=False))

    await ws.send_text(json.dumps(["EOSE", sub_id], separators=(",", ":"), ensure_ascii=False))


async def handle_close_message(ws: WebSocket, sub_id: str):
    async with STATE_LOCK:
        subs = CLIENT_SUBS.get(ws, {})
        subs.pop(sub_id, None)


async def broadcast_event_to_subscribers(event: Dict[str, Any]):
    """
    For each connected client and each subscription, if event matches any filter, push it.
    """
    async with STATE_LOCK:
        # Snapshot to avoid holding lock during network sends
        items = [(ws, dict(subs)) for ws, subs in CLIENT_SUBS.items()]

    for ws, subs in items:
        for sub_id, filters in subs.items():
            matched = any(event_matches_filter(event, flt) for flt in filters)
            if matched:
                try:
                    await ws.send_text(json.dumps(["EVENT", sub_id, event], separators=(",", ":"), ensure_ascii=False))
                except Exception:
                    pass


# -----------------------------
# WebSocket endpoint
# -----------------------------
@app.websocket("/")
async def nostr_ws(ws: WebSocket):
    await ws.accept()

    async with STATE_LOCK:
        CLIENT_SUBS.setdefault(ws, {})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                await send_notice(ws, "invalid JSON")
                continue

            if not isinstance(msg, list) or len(msg) == 0:
                await send_notice(ws, "invalid message format")
                continue

            mtype = msg[0]

            # ["EVENT", <event>]
            if mtype == "EVENT":
                if len(msg) != 2 or not isinstance(msg[1], dict):
                    await send_notice(ws, "EVENT must be ['EVENT', <event_object>]")
                    continue
                await handle_event_message(ws, msg[1])

            # ["REQ", <sub_id>, <filter1>, <filter2>, ...]
            elif mtype == "REQ":
                if len(msg) < 3 or not isinstance(msg[1], str):
                    await send_notice(ws, "REQ must be ['REQ', <sub_id>, <filter...>]")
                    continue
                sub_id = msg[1]
                filters = [f for f in msg[2:] if isinstance(f, dict)]
                if not filters:
                    await send_notice(ws, "REQ requires at least one filter object")
                    continue
                await handle_req_message(ws, sub_id, filters)

            # ["CLOSE", <sub_id>]
            elif mtype == "CLOSE":
                if len(msg) != 2 or not isinstance(msg[1], str):
                    await send_notice(ws, "CLOSE must be ['CLOSE', <sub_id>]")
                    continue
                await handle_close_message(ws, msg[1])

            else:
                await send_notice(ws, f"unsupported message type: {mtype}")

    except WebSocketDisconnect:
        pass
    finally:
        async with STATE_LOCK:
            CLIENT_SUBS.pop(ws, None)


# -----------------------------
# Health check
# -----------------------------
@app.get("/health")
def health():
    return {
        "ok": True,
        "time": int(time.time()),
        "events": len(EVENTS_LIST),
        "clients": len(CLIENT_SUBS),
    }