import json
import time
import hashlib
import os

import websocket
from secp256k1 import PrivateKey
from bech32 import bech32_decode, convertbits
from dotenv import load_dotenv

load_dotenv()

RELAY_URL = os.getenv("RELAY_URL", "wss://relay.damus.io")


def decode_nip19(bech: str) -> tuple[str, bytes]:
    hrp, data = bech32_decode(bech)
    if hrp is None or data is None:
        raise ValueError("Invalid bech32 string")
    decoded = convertbits(data, 5, 8, False)
    if decoded is None:
        raise ValueError("convertbits failed")
    return hrp, bytes(decoded)



NSEC = os.getenv("NOSTR_NSEC")   


if not NSEC:
    raise SystemExit("Missing NSEC in .env (e.g., NSEC=nsec1...)")

hrp_nsec, sk_bytes = decode_nip19(NSEC)
if hrp_nsec != "nsec" or len(sk_bytes) != 32:
    raise SystemExit("Invalid NSEC (must decode to 32 bytes and hrp 'nsec')")

# Create private key
privkey = PrivateKey(sk_bytes, raw=True)

# Derive x-only pubkey from the private key (Nostr expects 32-byte x-only hex)
pubkey_xonly = privkey.pubkey.serialize(compressed=True)[1:33].hex()


# Build event
created_at = int(time.time())
kind = 1
tags = []
content = "Hello Nostr, from my Python publisher 🚀"

# NIP-01 canonical preimage
event_data = [
    0, 
    pubkey_xonly, 
    created_at, 
    kind, 
    tags, 
    content
]
serialized = json.dumps(event_data, separators=(",", ":"), ensure_ascii=False)
event_id = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

# Schnorr sign (raw=True because msg is already 32 bytes)
sig = privkey.schnorr_sign(bytes.fromhex(event_id), None, raw=True).hex()

event = {
    "id": event_id,
    "pubkey": pubkey_xonly,
    "created_at": created_at,
    "kind": kind,
    "tags": tags,
    "content": content,
    "sig": sig
}


def on_open(ws):
    print("✅ Connected to relay:", RELAY_URL)
    ws.send(json.dumps(["EVENT", event], separators=(",", ":"), ensure_ascii=False))
    print("📤 Sent EVENT id:", event_id)


def on_message(ws, message):
    print("📩 Relay response:", message)
    # Optional: close after first OK response
    try:
        data = json.loads(message)
        if isinstance(data, list) and len(data) >= 4 and data[0] == "OK" and data[1] == event_id:
            ws.close()
    except Exception:
        pass


def on_error(ws, error):
    print("❌ Error:", error)


def on_close(ws, close_status_code, close_msg):
    print("🔌 Closed:", close_status_code, close_msg)


ws = websocket.WebSocketApp(
    RELAY_URL,
    on_open=on_open,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close
)

ws.run_forever()
