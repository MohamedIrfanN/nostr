import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

import websockets

from .config import RELAYS, PING_INTERVAL, PING_TIMEOUT
from .nip04 import nip04_decrypt
from .metadata import get_display_names


# ---------- helpers ----------

def _fmt_time(ts: int | None) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(int(ts)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _get_first_p_tag(event: dict) -> str | None:
    for t in event.get("tags", []) or []:
        if isinstance(t, list) and len(t) >= 2 and t[0] == "p":
            return t[1].lower()
    return None


def _extract_partner(my_pubkey: str, event: dict) -> tuple[str, str]:
    """
    Returns: (direction, partner_pubkey)
    """
    sender = (event.get("pubkey") or "").lower()
    recipient = _get_first_p_tag(event)

    if sender == my_pubkey:
        return "OUT", recipient or "unknown"
    return "IN", sender or "unknown"


def _decrypt(privkey, partner: str, content: str) -> str:
    if not partner or len(partner) != 64:
        return "(cannot decrypt)"
    try:
        return nip04_decrypt(privkey, partner, content)
    except Exception:
        return "(failed to decrypt)"


# ---------- PHASE A : inbox ----------

async def fetch_dm_inbox_7d(privkey, my_pubkey: str, blocked_set: set[str] | None = None) -> dict[str, dict]:
    """
    Returns:
      {
        partner_pubkey: {
            "last_ts": int,
            "preview": str
        }
      }
    """
    since = int(time.time()) - 7 * 24 * 60 * 60
    seen_ids: set[str] = set()
    conversations: dict[str, dict] = {}

    async def _from_relay(relay: str):
        sub_id = str(uuid.uuid4())
        req = [
            "REQ",
            sub_id,
            {"kinds": [4], "#p": [my_pubkey], "since": since, "limit": 500},
            {"kinds": [4], "authors": [my_pubkey], "since": since, "limit": 500},
        ]

        try:
            async with websockets.connect(
                relay,
                ping_interval=PING_INTERVAL,
                ping_timeout=PING_TIMEOUT,
            ) as ws:
                await ws.send(json.dumps(req, separators=(",", ":")))

                while True:
                    msg = json.loads(await ws.recv())
                    if msg[0] == "EOSE":
                        break

                    if msg[0] != "EVENT":
                        continue

                    event = msg[2]
                    eid = event.get("id")
                    if not eid or eid in seen_ids:
                        continue
                    seen_ids.add(eid)

                    direction, partner = _extract_partner(my_pubkey, event)
                    if partner in blocked_set:
                        return
                    ts = event.get("created_at") or 0
                    text = _decrypt(privkey, partner, event.get("content") or "")

                    cur = conversations.get(partner)
                    if not cur or ts > cur["last_ts"]:
                        conversations[partner] = {
                            "last_ts": ts,
                            "preview": text[:60],
                        }
        except Exception:
            pass

    await asyncio.gather(*(_from_relay(r) for r in RELAYS))

    # ---------- resolve names ----------
    # Resolve names for partners (best effort)
    partners = [pk for pk in conversations.keys() if pk != "unknown" and len(pk) == 64]
    names = await get_display_names(partners)

    for pk, info in conversations.items():
        if pk in names:
            info["name"] = names[pk]
        else:
            # fallback already OK
            info["name"] = pk[:12] + "…" if pk != "unknown" else "unknown"

    return conversations


# ---------- PHASE B : chat ----------

async def open_dm_chat(privkey, my_pubkey: str, partner: str, blocked_set: set[str] | None = None):
    """
    Chat with one partner:
      - Phase 1: load history (sorted)
      - Phase 2: live receive + send messages
    Type /back to return to inbox.
    """
    blocked_set = blocked_set or set()
    if partner in blocked_set:
        print("⚠️ This user is blocked. Unblock to open chat.")
        return
    history_since = int(time.time()) - 7 * 24 * 60 * 60
    live_since = int(time.time()) - 5

    seen_ids: set[str] = set()
    history_msgs: list[dict] = []

    from .events import build_signed_dm
    from .publish import publish_to_relays
    from .metadata import get_display_names

    # Resolve name once
    name_map = await get_display_names([partner])
    partner_name = name_map.get(partner, partner[:12] + "…")

    async def _fetch_history_from_relay(relay: str):
        sub_id = str(uuid.uuid4())
        req = [
            "REQ",
            sub_id,
            {"kinds": [4], "authors": [partner], "#p": [my_pubkey], "since": history_since, "limit": 500},
            {"kinds": [4], "authors": [my_pubkey], "#p": [partner], "since": history_since, "limit": 500},
        ]

        try:
            async with websockets.connect(
                relay,
                ping_interval=PING_INTERVAL,
                ping_timeout=PING_TIMEOUT,
            ) as ws:
                await ws.send(json.dumps(req, separators=(",", ":")))

                while True:
                    msg = json.loads(await ws.recv())
                    if msg[0] == "EOSE":
                        break

                    if msg[0] != "EVENT":
                        continue

                    event = msg[2]
                    eid = event.get("id")
                    if not eid or eid in seen_ids:
                        continue
                    seen_ids.add(eid)

                    direction, _ = _extract_partner(my_pubkey, event)
                    ts_int = int(event.get("created_at") or 0)
                    text = _decrypt(privkey, partner, event.get("content") or "")

                    history_msgs.append({
                        "created_at": ts_int,
                        "direction": direction,
                        "text": text,
                    })
        except Exception:
            pass

    async def _listen_live_from_relay(relay: str):
        sub_id = str(uuid.uuid4())
        req = [
            "REQ",
            sub_id,
            {"kinds": [4], "authors": [partner], "#p": [my_pubkey], "since": live_since},
            {"kinds": [4], "authors": [my_pubkey], "#p": [partner], "since": live_since},
        ]

        try:
            async with websockets.connect(
                relay,
                ping_interval=PING_INTERVAL,
                ping_timeout=PING_TIMEOUT,
            ) as ws:
                await ws.send(json.dumps(req, separators=(",", ":")))

                while True:
                    msg = json.loads(await ws.recv())
                    if msg[0] != "EVENT":
                        continue

                    event = msg[2]
                    eid = event.get("id")
                    if not eid or eid in seen_ids:
                        continue
                    seen_ids.add(eid)

                    direction, _ = _extract_partner(my_pubkey, event)
                    ts = _fmt_time(event.get("created_at"))
                    text = _decrypt(privkey, partner, event.get("content") or "")

                    print(f"\n[{direction}] {ts}  {text}")
                    print("You: ", end="", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _send_loop():
        while True:
            msg = await asyncio.to_thread(input, "You: ")
            msg = msg.strip()

            if not msg:
                continue

            if msg == "/back":
                raise asyncio.CancelledError

            eid, ev = build_signed_dm(privkey, partner, msg)
            seen_ids.add(eid) # prevent relay echo duplicates
            await publish_to_relays(eid, ev, quiet=True)

            # Print immediately for good UX
            print(f"[OUT] {_fmt_time(int(time.time()))}  {msg}")

    print(f"\n💬 Chat with {partner_name}")
    print("Loading history...\n")

    # ---------- Phase 1: history ----------
    await asyncio.gather(*(_fetch_history_from_relay(r) for r in RELAYS))
    history_msgs.sort(key=lambda x: x["created_at"])

    for m in history_msgs:
        print(f"[{m['direction']}] {_fmt_time(m['created_at'])}  {m['text']}")

    print("\n--- Live chat (type /back to return) ---\n")

    # ---------- Phase 2: live + send ----------
    tasks = []
    for r in RELAYS:
        tasks.append(asyncio.create_task(_listen_live_from_relay(r)))
    tasks.append(asyncio.create_task(_send_loop()))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        print("\n↩️ back to inbox")


