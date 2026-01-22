from secp256k1 import PrivateKey, PublicKey
import time
import json
import hashlib

# Key generation
priv = PrivateKey()  # random private key
sk_hex = priv.private_key.hex()

pub_obj = priv.pubkey  # PublicKey object

# compressed pubkey bytes: 33 bytes
pub_compressed = pub_obj.serialize(compressed=True)  # bytes
pubkey_xonly = pub_compressed[1:33]  # drop 02/03 prefix -> X only (32 bytes)
pubkey_hex = pubkey_xonly.hex()

print("Private key (hex):", sk_hex)
print("Public key  (hex):", pubkey_hex)

# Unsigned event
created_at = int(time.time())
kind = 1
tags = []
content = "Hello (NIP-01 event skeleton)"

# NIP-01 event id
event = [
    0, 
    pubkey_hex, 
    created_at, 
    kind, 
    tags, 
    content
]

event_json = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
event_id = hashlib.sha256(event_json.encode("utf-8")).hexdigest()

print("\nCanonical event JSON (hashed):")
print(event_json)
print("\nDerived event id (sha256 hex):")
print(event_id)

# Schnorr sign id
sig_hex = priv.schnorr_sign(
    bytes.fromhex(event_id),
    None,
    raw=True
).hex()

signed_event = {
    "id": event_id,
    "pubkey": pubkey_hex,
    "created_at": created_at,
    "kind": kind,
    "tags": tags,
    "content": content,
    "sig": sig_hex,
}

print("\nFinal event (id + sig):")
print(signed_event)

# Verify signature
pub_verify = PublicKey(bytes.fromhex("02" + signed_event["pubkey"]), raw=True)

is_valid = pub_verify.schnorr_verify(
    bytes.fromhex(signed_event["id"]),
    bytes.fromhex(signed_event["sig"]),
    None,
    raw=True
)

print("\nSignature valid:", is_valid)
