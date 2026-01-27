import hashlib
import json
import time
from secp256k1 import PrivateKey
from .utils import pubkey_xonly_hex
from .nip04 import nip04_encrypt
from .utils import normalize_pubkey_input
from .utils import require_32byte_hex, is_32byte_hex


# NIP-01 event id from fields
def _event_id_from_fields(pubkey: str, created_at: int, kind: int, tags: list, content: str) -> str:
    event_data = [0, pubkey, created_at, kind, tags, content]
    serialized = json.dumps(event_data, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

# NIP-01 sign event
def _sign_event(privkey: PrivateKey, event_id_hex: str) -> str:
    return privkey.schnorr_sign(bytes.fromhex(event_id_hex), None, raw=True).hex()

# NIP-01 text note (kind:1)
def build_signed_text_note(privkey: PrivateKey, content: str) -> tuple[str, dict]:
    """
    Build and sign a kind:1 text note event.
    """
    pubkey = pubkey_xonly_hex(privkey)
    created_at = int(time.time())
    kind = 1
    tags: list[list[str]] = []

    event_id = _event_id_from_fields(pubkey, created_at, kind, tags, content)
    sig = _sign_event(privkey, event_id)

    event = {
        "id": event_id,
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig,
    }
    return event_id, event

# NIP-02 contacts (kind:3)
def build_signed_contacts_event(
    privkey: PrivateKey,
    followed_pubkeys: list[str],
    content: str = "",
) -> tuple[str, dict]:
    """
    Build and sign a kind:3 contacts event (classic NIP-02 style).
    Tags: [["p", <pubkey>], ...]
    """
    pubkey = pubkey_xonly_hex(privkey)
    created_at = int(time.time())
    kind = 3

    # normalize + dedupe + stable order
    clean = []
    seen = set()
    for pk in followed_pubkeys:
        pk = (pk or "").strip().lower()
        if is_32byte_hex(pk) and pk not in seen:
            seen.add(pk)
            clean.append(pk)
    clean.sort()

    tags = [["p", pk] for pk in clean]

    event_id = _event_id_from_fields(pubkey, created_at, kind, tags, content)
    sig = _sign_event(privkey, event_id)

    event = {
        "id": event_id,
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig,
    }
    return event_id, event


# NIP-25 reaction (kind:7)
def build_signed_reaction(
    privkey: PrivateKey,
    target_event_id: str,
    target_pubkey: str | None = None,
    reaction: str = "+",
) -> tuple[str, dict]:
    """
    NIP-25 reaction (kind:7)
    - content: "+" (like) by default
    - tags: [["e", <event_id>]] and optionally ["p", <pubkey>]
    """
    pubkey = pubkey_xonly_hex(privkey)
    created_at = int(time.time())
    kind = 7

    eid = require_32byte_hex(target_event_id, "event id")

    tags: list[list[str]] = [["e", eid]]

    if target_pubkey:
        pk = (target_pubkey or "").strip().lower()
        if is_32byte_hex(pk):
            tags.append(["p", pk])

    content = (reaction or "+").strip() or "+"

    event_id = _event_id_from_fields(pubkey, created_at, kind, tags, content)
    sig = _sign_event(privkey, event_id)

    event = {
        "id": event_id,
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig,
    }
    return event_id, event

# NIP-10 comment (kind:1)
def build_signed_comment(
    privkey: PrivateKey,
    target_event: dict,
    content: str,
) -> tuple[str, dict]:
    """
    Create a comment (reply) to a target event using NIP-10 style tags.

    - kind: 1
    - tags include:
        ["e", <root_id>, "", "root"]
        ["e", <reply_to_id>, "", "reply"]
        ["p", <reply_to_author_pubkey>]
    """
    pubkey = pubkey_xonly_hex(privkey)
    created_at = int(time.time())
    kind = 1

    reply_to_id = require_32byte_hex(target_event.get("id"), "Target event id")
    reply_to_author = (target_event.get("pubkey") or "").strip().lower()

    # Find root id from target's tags if present; otherwise root is the target itself
    root_id = None
    for t in target_event.get("tags", []) or []:
        # NIP-10 marker: ["e", <id>, <relay?>, "root"]
        if isinstance(t, list) and len(t) >= 4 and t[0] == "e" and t[3] == "root":
            cand = (t[1] or "").strip().lower()
            if is_32byte_hex(cand):
                root_id = cand
                break

    if not root_id:
        root_id = reply_to_id

    tags: list[list[str]] = [
        ["e", root_id, "", "root"],
        ["e", reply_to_id, "", "reply"],
    ]

    if is_32byte_hex(reply_to_author):
        tags.append(["p", reply_to_author])

    content = (content or "").strip()
    if not content:
        raise ValueError("Comment content cannot be empty")

    event_id = _event_id_from_fields(pubkey, created_at, kind, tags, content)
    sig = _sign_event(privkey, event_id)

    event = {
        "id": event_id,
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig,
    }
    return event_id, event


# NIP-04 DM (kind:4)
def build_signed_dm(privkey: PrivateKey, recipient_pubkey_input: str, plaintext: str) -> tuple[str, dict]:
    """
    NIP-04 DM: kind 4
    tags: [["p", recipient_pubkey]]
    content: encrypted string
    """
    pubkey = pubkey_xonly_hex(privkey)
    created_at = int(time.time())
    kind = 4

    recipient_pubkey = normalize_pubkey_input(recipient_pubkey_input)

    if not plaintext.strip():
        raise ValueError("DM message cannot be empty")

    encrypted = nip04_encrypt(privkey, recipient_pubkey, plaintext)

    tags: list[list[str]] = [["p", recipient_pubkey]]
    content = encrypted

    event_id = _event_id_from_fields(pubkey, created_at, kind, tags, content)
    sig = _sign_event(privkey, event_id)

    event = {
        "id": event_id,
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig,
    }
    return event_id, event


