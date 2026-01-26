import asyncio
import json
import time
import uuid
import websockets
import os
import hashlib
from dotenv import load_dotenv

from bech32 import bech32_decode, convertbits
from secp256k1 import PrivateKey


RELAYS = [
    "wss://relay.damus.io",
    "wss://relay.snort.social",
    "wss://nos.lol",
    "ws://localhost:8000",
]

# Track events we’ve already printed (dedupe across relays)
seen_ids: set[str] = set()


def decode_nip19(bech: str) -> tuple[str, bytes]:
    hrp, data = bech32_decode(bech)
    if hrp is None or data is None:
        raise ValueError("Invalid bech32 string")
    decoded = convertbits(data, 5, 8, False)
    if decoded is None:
        raise ValueError("convertbits failed")
    return hrp, bytes(decoded)


def get_pubkey_xonly_from_env() -> str:
    load_dotenv()
    nsec = os.getenv("NOSTR_NSEC")
    if not nsec:
        raise SystemExit("Missing NOSTR_NSEC in .env (e.g., NOSTR_NSEC=nsec1...)")

    hrp_nsec, sk_bytes = decode_nip19(nsec)
    if hrp_nsec != "nsec" or len(sk_bytes) != 32:
        raise SystemExit("Invalid NOSTR_NSEC (must decode to 32 bytes and hrp 'nsec')")

    privkey = PrivateKey(sk_bytes, raw=True)
    # x-only pubkey (32 bytes) in hex
    return privkey.pubkey.serialize(compressed=True)[1:33].hex()


def build_signed_text_note(content: str) -> tuple[str, dict]:
    """
    Builds and signs a NIP-01 kind:1 event
    Returns (event_id, event_dict).
    """
    load_dotenv()
    nsec = os.getenv("NOSTR_NSEC")
    if not nsec:
        raise SystemExit("Missing NOSTR_NSEC in .env (e.g., NOSTR_NSEC=nsec1...)")

    hrp_nsec, sk_bytes = decode_nip19(nsec)
    if hrp_nsec != "nsec" or len(sk_bytes) != 32:
        raise SystemExit("Invalid NOSTR_NSEC (must decode to 32 bytes and hrp 'nsec')")

    privkey = PrivateKey(sk_bytes, raw=True)
    pubkey_xonly = privkey.pubkey.serialize(compressed=True)[1:33].hex()

    created_at = int(time.time())
    kind = 1
    tags = []

    event_data = [0, pubkey_xonly, created_at, kind, tags, content]
    serialized = json.dumps(event_data, separators=(",", ":"), ensure_ascii=False)
    event_id = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    sig = privkey.schnorr_sign(bytes.fromhex(event_id), None, raw=True).hex()

    event = {
        "id": event_id,
        "pubkey": pubkey_xonly,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig,
    }
    return event_id, event


# ----------------- PUBLISH (multi-relay) -----------------
async def publish_to_relay(relay_url: str, event_id: str, event: dict) -> bool:
    """
    Connects to a relay, publishes the event, waits for OK, and closes.
    """
    try:
        async with websockets.connect(relay_url, ping_interval=20, ping_timeout=20) as ws:
            print(f"\n✅ Connected (publish): {relay_url}")
            await ws.send(json.dumps(["EVENT", event], separators=(",", ":"), ensure_ascii=False))
            print(f"📤 Sent EVENT to {relay_url} id={event_id[:12]}…")

            while True:
                raw = await ws.recv()
                msg = json.loads(raw)

                if isinstance(msg, list) and len(msg) >= 4 and msg[0] == "OK" and msg[1] == event_id:
                    accepted = bool(msg[2])
                    reason = msg[3]
                    if accepted:
                        print(f"✅ OK accepted by {relay_url}: {reason}")
                    else:
                        print(f"❌ OK rejected by {relay_url}: {reason}")
                    return accepted

                if isinstance(msg, list) and len(msg) >= 2 and msg[0] == "NOTICE":
                    print(f"⚠️  NOTICE from {relay_url}: {msg[1]}")

    except Exception as e:
        print(f"\n❌ Publish error: {relay_url}\n   {type(e).__name__}: {e}")
        return False


async def publish_to_relays(content: str):
    event_id, event = build_signed_text_note(content)
    print("\n==============================")
    print("🚀 Publishing to all relays...")
    print(f"Event id: {event_id}")
    print("==============================")

    results = await asyncio.gather(*(publish_to_relay(r, event_id, event) for r in RELAYS))
    ok = sum(1 for x in results if x)
    print(f"\n📌 Publish summary: accepted by {ok}/{len(RELAYS)} relays")


# ----------------- READ (multi-relay) -----------------
async def read_from_relay(relay_url: str, pubkey_xonly: str):
    # Put pubkey into filter
    filter_obj = {
        "authors": [pubkey_xonly],
        "kinds": [1],
        "limit": 50,
        "since": int(time.time()) - 60 * 60,
    }

    sub_id = str(uuid.uuid4())
    req_msg = ["REQ", sub_id, filter_obj]

    try:
        async with websockets.connect(relay_url, ping_interval=20, ping_timeout=20) as ws:
            print(f"\n✅ Connected (read): {relay_url}")
            await ws.send(json.dumps(req_msg))
            print(f"📤 Sent REQ (sub_id={sub_id}) authors={pubkey_xonly[:12]}…")

            # Run until Ctrl+C (don’t return on EOSE)
            while True:
                raw = await ws.recv()
                msg = json.loads(raw)

                if not msg:
                    continue

                msg_type = msg[0]

                if msg_type == "EVENT":
                    _, got_sub_id, event = msg
                    if got_sub_id != sub_id:
                        continue

                    eid = event.get("id")
                    if not eid or eid in seen_ids:
                        continue
                    seen_ids.add(eid)

                    created_at = event.get("created_at")
                    content = event.get("content", "").replace("\n", " ").strip()

                    print(f"\n🟦 EVENT from {relay_url}")
                    print(f"  id: {eid[:12]}…")
                    print(f"  created_at: {created_at}")
                    print(f"  content: {content[:200]}")

                elif msg_type == "EOSE":
                    # IMPORTANT: Don't close. Just acknowledge and keep listening for new events.
                    _, got_sub_id = msg
                    if got_sub_id == sub_id:
                        print(f"✅ EOSE from {relay_url} (now listening for live events...)")

                elif msg_type == "NOTICE":
                    print(f"🟨 Notice from {relay_url}: {msg[1]}")

    except asyncio.CancelledError:
        # Allow clean task cancel on Ctrl+C
        raise
    except Exception as e:
        print(f"\n❌ Read error: {relay_url}\n   {type(e).__name__}: {e}")


async def read_from_all_relays(pubkey_xonly: str):
    print("\n==============================")
    print("📡 Reading from all relays (Ctrl+C to stop)...")
    print("==============================")
    await asyncio.gather(*(read_from_relay(r, pubkey_xonly) for r in RELAYS))


async def main():
    pubkey_xonly = get_pubkey_xonly_from_env()

    # 1) Publish
    await publish_to_relays("Hi Nostr, Published to multiple relays")

    # 2) Read (keep running until Ctrl+C)
    await read_from_all_relays(pubkey_xonly)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Stopped")
