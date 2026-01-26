import os
import base64
import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.asymmetric import ec


def _sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def _aes_cbc_encrypt(key32: bytes, iv16: bytes, plaintext: bytes) -> bytes:
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()

    cipher = Cipher(algorithms.AES(key32), modes.CBC(iv16))
    enc = cipher.encryptor()
    return enc.update(padded) + enc.finalize()


def _aes_cbc_decrypt(key32: bytes, iv16: bytes, ciphertext: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key32), modes.CBC(iv16))
    dec = cipher.decryptor()
    padded = dec.update(ciphertext) + dec.finalize()

    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _privkey_bytes(privkey) -> bytes:
    """
    Extract 32-byte secret from your secp256k1.PrivateKey object.
    Different bindings expose this differently; we try common options.
    """
    if hasattr(privkey, "private_key"):
        sk = privkey.private_key
        if isinstance(sk, (bytes, bytearray)) and len(sk) == 32:
            return bytes(sk)

    if hasattr(privkey, "serialize"):
        sk = privkey.serialize()
        if isinstance(sk, (bytes, bytearray)) and len(sk) == 32:
            return bytes(sk)

    # last resort: sometimes it's stored on a private attribute
    for name in ("_private_key", "_secret", "secret"):
        if hasattr(privkey, name):
            sk = getattr(privkey, name)
            if isinstance(sk, (bytes, bytearray)) and len(sk) == 32:
                return bytes(sk)

    raise ValueError("Could not extract 32-byte secret from PrivateKey")


def _recipient_compressed_pubkey(recipient_pubkey_hex: str) -> bytes:
    """
    Nostr pubkeys are 32-byte x-only (BIP340). For ECDH we need a full EC point.
    BIP340 uses 'lift_x' with EVEN Y. A compressed key with even y has prefix 0x02.
    So we can represent it as: 0x02 || x
    """
    x = bytes.fromhex(recipient_pubkey_hex)
    if len(x) != 32:
        raise ValueError("recipient pubkey must be 32 bytes (64-hex)")
    return b"\x02" + x


def nip04_shared_key(privkey, recipient_pubkey_hex: str) -> bytes:
    """
    NIP-04: use ONLY the X coordinate of the ECDH shared point (32 bytes) as the AES key.
    Do NOT hash it.
    """
    recipient_pubkey_hex = recipient_pubkey_hex.strip().lower()
    if len(recipient_pubkey_hex) != 64 or not all(c in "0123456789abcdef" for c in recipient_pubkey_hex):
        raise ValueError("recipient pubkey must be 64-hex")

    sk = _privkey_bytes(privkey)
    sk_int = int.from_bytes(sk, "big")

    priv = ec.derive_private_key(sk_int, ec.SECP256K1())
    pub_bytes = _recipient_compressed_pubkey(recipient_pubkey_hex)  # b"\x02"+x (even Y)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), pub_bytes)

    shared = priv.exchange(ec.ECDH(), pub)  # cryptography returns 32-byte X coordinate
    if len(shared) != 32:
        raise ValueError(f"ECDH shared secret unexpected length: {len(shared)}")
    return shared


def nip04_encrypt(privkey, recipient_pubkey_hex: str, plaintext: str) -> str:
    """
    Returns: base64(ciphertext)?iv=base64(iv)
    """
    key = nip04_shared_key(privkey, recipient_pubkey_hex)
    iv = os.urandom(16)
    ct = _aes_cbc_encrypt(key, iv, plaintext.encode("utf-8"))

    b64_ct = base64.b64encode(ct).decode("ascii")
    b64_iv = base64.b64encode(iv).decode("ascii")
    return f"{b64_ct}?iv={b64_iv}"


def nip04_decrypt(privkey, sender_pubkey_hex: str, content: str) -> str:
    if "?iv=" not in content:
        raise ValueError("Invalid NIP-04 content (missing ?iv=)")

    b64_ct, b64_iv = content.split("?iv=", 1)
    ct = base64.b64decode(b64_ct)
    iv = base64.b64decode(b64_iv)

    key = nip04_shared_key(privkey, sender_pubkey_hex)
    pt = _aes_cbc_decrypt(key, iv, ct)
    return pt.decode("utf-8", errors="replace")
