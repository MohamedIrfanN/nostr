# zap.py
import asyncio
import json
import urllib.parse
import urllib.request

from .profile_search import fetch_profile_by_pubkey
from .utils import normalize_pubkey_input
from .events import build_signed_zap_request
from .config import RELAYS


def _http_get_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


async def _get_json(url: str, timeout: float = 8.0) -> dict:
    return await asyncio.to_thread(_http_get_json, url, timeout)


def _lnurlp_url_from_lud16(lud16: str) -> str:
    # lud16 = "name@domain.com"
    name, domain = lud16.split("@", 1)
    return f"https://{domain}/.well-known/lnurlp/{name}"


async def generate_zap_invoice(
    privkey,
    recipient_pubkey_input: str,
    sats: int,
) -> tuple[str, dict]:
    """
    Returns (bolt11_invoice, debug_info)
    """
    if not isinstance(sats, int) or sats <= 0:
        raise ValueError("sats must be a positive integer")

    recipient_pubkey = normalize_pubkey_input(recipient_pubkey_input)
    amount_msat = sats * 1000

    # 1) fetch profile to get lud16/lud06
    prof = await fetch_profile_by_pubkey(recipient_pubkey)
    if not prof:
        raise ValueError("Profile not found on your relays (cannot discover lud16/lud06)")

    lud16 = (prof.get("lud16") or "").strip()
    lud06 = (prof.get("lud06") or "").strip()

    if not lud16 and not lud06:
        raise ValueError("User profile has no lud16/lud06 (no zap endpoint)")

    # 2) resolve lnurl-pay endpoint
    # For minimal implementation we support lud16 directly.
    # (lud06 is a bech32 LNURL string — we can add it later if you want.)
    if lud16:
        lnurlp = _lnurlp_url_from_lud16(lud16)
        lnurl_for_tag = lnurlp  # we can store URL string as "lnurl" tag
    else:
        raise ValueError("lud06 not supported in this minimal version (tell me and we’ll add it)")

    payinfo = await _get_json(lnurlp)
    callback = payinfo.get("callback")
    min_msat = int(payinfo.get("minSendable", 0))
    max_msat = int(payinfo.get("maxSendable", 0))

    if not callback:
        raise ValueError("LNURL-pay endpoint missing callback")
    if amount_msat < min_msat or (max_msat and amount_msat > max_msat):
        raise ValueError(f"Amount out of range. min={min_msat}msat max={max_msat}msat")

    # 3) build zap request (kind 9734)
    _, zap_req = build_signed_zap_request(
        privkey=privkey,
        recipient_pubkey_hex=recipient_pubkey,
        amount_msat=amount_msat,
        lnurl=lnurl_for_tag,
        relays=RELAYS,
        target_event_id=None,  # later you can allow zapping a specific note id
    )

    # 4) call callback to get invoice
    params = {
        "amount": str(amount_msat),
        "nostr": json.dumps(zap_req, separators=(",", ":"), ensure_ascii=False),
    }
    cb_url = callback + ("&" if "?" in callback else "?") + urllib.parse.urlencode(params)

    resp = await _get_json(cb_url)
    pr = resp.get("pr")  # bolt11 invoice
    if not pr:
        # some servers return {"status":"ERROR","reason":"..."}
        reason = resp.get("reason") or resp.get("status") or str(resp)
        raise ValueError(f"LNURL callback did not return invoice: {reason}")

    debug = {
        "recipient_pubkey": recipient_pubkey,
        "lud16": lud16,
        "lnurlp": lnurlp,
        "callback": callback,
        "amount_msat": amount_msat,
    }
    return pr, debug
