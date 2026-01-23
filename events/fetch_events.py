import json
import os
import time

import websocket
from dotenv import load_dotenv
from bech32 import bech32_decode, convertbits
from secp256k1 import PrivateKey

load_dotenv()

RELAY_URL = os.getenv("RELAY_URL", "wss://relay.damus.io")
NSEC = os.getenv("NOSTR_NSEC")
if not NSEC:
    raise SystemExit("Missing NSEC in .env (NSEC=nsec1...)")


LIMIT = 10


def decode_nip19(bech: str) -> tuple[str, bytes]:
    hrp, data = bech32_decode(bech.strip())
    if hrp is None or data is None:
        raise ValueError("Invalid bech32")
    decoded = convertbits(data, 5, 8, False)
    if decoded is None:
        raise ValueError("convertbits failed")
    return hrp, bytes(decoded)


# Decode nsec -> secret key bytes
hrp, sk_bytes = decode_nip19(NSEC)
if hrp != "nsec" or len(sk_bytes) != 32:
    raise SystemExit("Invalid NSEC")

# Derive x-only pubkey from the secret key (author key)
priv = PrivateKey(sk_bytes, raw=True)
pubkey_xonly = priv.pubkey.serialize(compressed=True)[1:33].hex()
print("Relay:", RELAY_URL)
print("Author pubkey:", pubkey_xonly)
print("Limit:", LIMIT)

sub_id = f"my-events-{int(time.time())}"


def on_open(ws):
    print("✅ Connected. Sending REQ...")

    # Fetch events authored by your pubkey
    req = ["REQ", sub_id, {"authors": [pubkey_xonly], "kinds": [1],"limit": LIMIT}]

    ws.send(json.dumps(req, separators=(",", ":"), ensure_ascii=False))


def on_message(ws, message):
    try:
        data = json.loads(message)
    except Exception:
        print("Non-JSON message:", message)
        return

    # Expected message types: ["EVENT", sub_id, event], ["EOSE", sub_id]
    if isinstance(data, list) and len(data) >= 2:
        msg_type = data[0]

        if msg_type == "EVENT" and len(data) == 3:
            _, sid, event = data
            if sid != sub_id:
                return

            # Print a compact summary
            kind = event.get("kind")
            created_at = event.get("created_at")
            eid = event.get("id")
            content = event.get("content", "")

            print("\n--- EVENT ---")
            print("id:", eid)
            print("kind:", kind, "created_at:", created_at)
            print("content:", content)

        elif msg_type == "EOSE" and len(data) == 2:
            _, sid = data
            if sid == sub_id:
                print("\n✅ EOSE received (end of stored events). Closing.")
                ws.close()

        elif msg_type == "NOTICE":
            print("NOTICE:", data)

        # OK is usually for publishing, but we print if any appear
        elif msg_type == "OK":
            print("OK:", data)


def on_error(ws, error):
    print("❌ Error:", error)


def on_close(ws, code, msg):
    print("🔌 Closed:", code, msg)


ws = websocket.WebSocketApp(
    RELAY_URL,
    on_open=on_open,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close,
)

ws.run_forever()
