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


def get_privkey_from_env() -> PrivateKey:
    load_dotenv()
    nsec = os.getenv("NOSTR_NSEC")
    if not nsec:
        raise ValueError("Missing NOSTR_NSEC in .env")

    hrp, sk_bytes = decode_nip19(nsec)
    if hrp != "nsec" or len(sk_bytes) != 32:
        raise ValueError("Invalid NOSTR_NSEC")

    return PrivateKey(sk_bytes, raw=True)

def is_32byte_hex(s: str | None) -> bool:
    if not s:
        return False
    s = s.strip().lower()
    if len(s) != 64:
        return False
    try:
        bytes.fromhex(s)
        return True
    except ValueError:
        return False


def require_32byte_hex(s: str | None, label: str) -> str:
    """
    Validate and return normalized lowercase 64-hex (32 bytes).
    """
    if not is_32byte_hex(s):
        raise ValueError(f"{label} must be 64-hex (32 bytes)")
    return s.strip().lower()



def pubkey_xonly_hex(privkey: PrivateKey) -> str:
    return privkey.pubkey.serialize(compressed=True)[1:33].hex()

def normalize_pubkey_input(s: str) -> str:
    """
    Accepts either:
      - 64-hex pubkey
      - npub1... (NIP-19)
    Returns 64-hex pubkey (lowercase).
    """
    s = (s or "").strip()

    # If user pasted hex already
    if is_32byte_hex(s):
        return s.strip().lower()

    # If user pasted npub
    if s.startswith("npub1"):
        hrp, data = decode_nip19(s)
        if hrp != "npub" or len(data) != 32:
            raise ValueError("Invalid npub (must decode to 32 bytes)")
        return data.hex()

    raise ValueError("pubkey must be 64-hex or npub1...")

