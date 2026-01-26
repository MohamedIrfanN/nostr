import os
from dotenv import load_dotenv
from bech32 import bech32_decode, convertbits
from secp256k1 import PrivateKey

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
        # For the sake of the UI running even without env, we might want to handle this gracefully or fail fast.
        # The original script raised SystemExit.
        raise ValueError("Missing NOSTR_NSEC in .env (e.g., NOSTR_NSEC=nsec1...)")

    hrp_nsec, sk_bytes = decode_nip19(nsec)
    if hrp_nsec != "nsec" or len(sk_bytes) != 32:
        raise ValueError("Invalid NOSTR_NSEC (must decode to 32 bytes and hrp 'nsec')")

    privkey = PrivateKey(sk_bytes, raw=True)
    # x-only pubkey (32 bytes) in hex
    return privkey.pubkey.serialize(compressed=True)[1:33].hex()
