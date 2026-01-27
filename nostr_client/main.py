import asyncio
from .lookup import fetch_event_by_id_all_relays
from .config import RELAYS
from .utils import get_privkey_from_env, pubkey_xonly_hex
from .utils import require_32byte_hex
from .events import build_signed_text_note, build_signed_contacts_event
from .publish import publish_to_relays
from .utils import normalize_pubkey_input
from .dm_subscribe import fetch_dm_inbox_7d, open_dm_chat, _fmt_time
import time
import websockets
from .contacts import (
    fetch_following_all_relays,
    apply_follow,
    apply_unfollow,
)


def print_header(title: str):
    print("\n" + "=" * 40)
    print(title)
    print("=" * 40)


async def show_status(my_pubkey: str):
    print_header("STATUS")

    async def check_relay(relay: str, timeout_sec: float = 3.0):
        t0 = time.perf_counter()
        try:
            ws = await asyncio.wait_for(
                websockets.connect(relay, ping_interval=20, ping_timeout=20),
                timeout=timeout_sec,
            )
            dt_ms = (time.perf_counter() - t0) * 1000
            await ws.close()
            return relay, True, dt_ms, None
        except Exception as e:
            dt_ms = (time.perf_counter() - t0) * 1000
            return relay, False, dt_ms, f"{type(e).__name__}: {e}"

    print("Relays:")
    results = await asyncio.gather(*(check_relay(r) for r in RELAYS))

    for relay, ok, dt_ms, err in results:
        if ok:
            print(f" - {relay:<28} ✅ connected ({dt_ms:.0f} ms)")
        else:
            # keep error short
            short_err = (err or "").splitlines()[0]
            print(f" - {relay:<28} ❌ failed ({dt_ms:.0f} ms)  {short_err}")

    follows = await fetch_following_all_relays(my_pubkey)
    print(f"\nFollowing count: {len(follows)}")
    return follows


async def do_publish(privkey):
    print_header("PUBLISH")
    content = input("Enter content: ").strip()
    if not content:
        print("⚠️ empty content, cancelled")
        return
    eid, ev = build_signed_text_note(privkey, content)
    await publish_to_relays(eid, ev)


async def do_events(my_pubkey: str):
    """
    This uses your existing subscribe.py implementation (following-only stream).
    """
    print_header("EVENTS")
    print("Fetching contacts...")
    from .subscribe import read_from_all_relays_following  # imported here to keep main small

    follows = await fetch_following_all_relays(my_pubkey)
    authors = list(follows | {my_pubkey})
    print(f"Subscribing authors={len(authors)} (following={len(follows)})")
    print("Ctrl+C to stop events and return to menu.\n")

    try:
        await read_from_all_relays_following(authors)
    except KeyboardInterrupt:
        print("\n↩️ returning to menu")


async def do_follow(privkey, my_pubkey: str):
    print_header("FOLLOW")
    pk_input = input("Enter pubkey (64-hex or npub1...): ").strip()

    try:
        pk = normalize_pubkey_input(pk_input)
    except ValueError as e:
        print(f"⚠️ {e}")
        return

    current = await fetch_following_all_relays(my_pubkey)
    updated = apply_follow(current, pk)

    eid, ev = build_signed_contacts_event(privkey, sorted(updated))
    await publish_to_relays(eid, ev)
    print(f"✅ Now following: {len(updated)}")



async def do_unfollow(privkey, my_pubkey: str):
    print_header("UNFOLLOW")
    pk_input = input("Enter pubkey (64-hex or npub1...): ").strip()

    try:
        pk = normalize_pubkey_input(pk_input)
    except ValueError as e:
        print(f"⚠️ {e}")
        return

    current = await fetch_following_all_relays(my_pubkey)
    updated = apply_unfollow(current, pk)

    eid, ev = build_signed_contacts_event(privkey, sorted(updated))
    await publish_to_relays(eid, ev)
    print(f"✅ Now following: {len(updated)}")



async def do_reaction(privkey):
    print_header("REACTION (LIKE)")
    target_id_in = input("Enter event id (64-hex): ").strip()

    # 1) Validate
    try:
        target_id = require_32byte_hex(target_id_in, "event id")
    except ValueError as e:
        print(f"⚠️ {e}")
        return

    # 2) Optional lookup to add ["p", author] tag
    target_pubkey = None
    try:
        ev = await fetch_event_by_id_all_relays(target_id)
        if ev and isinstance(ev, dict):
            target_pubkey = ev.get("pubkey")
            if target_pubkey:
                print(f"Found target author pubkey: {target_pubkey[:12]}…")
            else:
                print("Target event found, but pubkey missing (will react with only e-tag).")
        else:
            print("Target event not found on our relays (will react with only e-tag).")
    except Exception as e:
        print(f"Lookup failed (will react with only e-tag): {e}")

    # 3) Build + publish reaction
    from .events import build_signed_reaction
    eid, reaction_event = build_signed_reaction(
        privkey,
        target_event_id=target_id,
        target_pubkey=target_pubkey,
        reaction="+",
    )
    await publish_to_relays(eid, reaction_event)



async def do_comment(privkey):
    print_header("COMMENT")
    target_id_in = input("Enter event id (64-hex): ").strip()
    # Validate event id
    try:
        target_id = require_32byte_hex(target_id_in, "event id")
    except ValueError as e:
        print(f"⚠️ {e}")
        return

    comment_text = input("Enter comment text: ").strip()

    if not comment_text:
        print("⚠️ comment cannot be empty")
        return

    # Lookup the target event (to build correct NIP-10 reply tags)
    from .lookup import fetch_event_by_id_all_relays
    target = await fetch_event_by_id_all_relays(target_id)

    if not target:
        print("❌ Target event not found on our relays. Cannot build proper reply tags.")
        print("Tip: try a relay that has the event, or paste a different event id.")
        return

    from .events import build_signed_comment
    eid, ev = build_signed_comment(privkey, target, comment_text)
    await publish_to_relays(eid, ev)




async def do_dm(privkey):
    print_header("DM (NIP-04)")

    recipient_in = input("Enter recipient pubkey (64-hex or npub1...): ").strip()

    try:
        recipient_hex = normalize_pubkey_input(recipient_in)
    except ValueError as e:
        print(f"⚠️ {e}")
        return

    msg = input("Enter message: ").strip()
    if not msg:
        print("⚠️ message cannot be empty")
        return

    from .events import build_signed_dm
    eid, ev = build_signed_dm(privkey, recipient_hex, msg)
    await publish_to_relays(eid, ev)



async def do_dm_inbox(privkey, my_pubkey: str):
    print_header("DM INBOX")

    inbox = await fetch_dm_inbox_7d(privkey, my_pubkey)
    if not inbox:
        print("No DMs in last 7 days.")
        return

    partners = sorted(
        inbox.items(),
        key=lambda x: x[1]["last_ts"],
        reverse=True,
    )

    print("\nDM Inbox (last 7 days):")
    for i, (pk, info) in enumerate(partners, 1):
        name = info.get("name", pk[:12] + "…")
        print(f"{i}) {name}  {_fmt_time(info['last_ts'])}  {info['preview']}")

    sel = input("\nSelect chat (number, blank to cancel): ").strip()
    if not sel:
        return
    if not sel.isdigit():
        print("⚠️ enter a number")
        return

    idx = int(sel) - 1
    if idx < 0 or idx >= len(partners):
        print("⚠️ invalid selection")
        return

    partner_pubkey = partners[idx][0]

    try:
        await open_dm_chat(privkey, my_pubkey, partner_pubkey)
    except KeyboardInterrupt:
        print("\n↩️ back to inbox")


async def do_dm_menu(privkey, my_pubkey: str):
    print_header("DM MENU")
    print("1) New DM")
    print("2) DM Inbox and Chat")
    print("0) Back")

    choice = input("\nSelect: ").strip()
    if choice == "1":
        await do_dm(privkey)
    elif choice == "2":
        await do_dm_inbox(privkey, my_pubkey)
    elif choice == "0":
        return
    else:
        print("⚠️ invalid option")


async def do_delete(privkey):
    print_header("DELETE (NIP-09)")
    target_id_in = input("Enter event id to delete (64-hex): ").strip()

    try:
        target_id = require_32byte_hex(target_id_in, "event id")
    except ValueError as e:
        print(f"⚠️ {e}")
        return

    from .events import build_signed_delete
    eid, ev = build_signed_delete(privkey, target_id)

    await publish_to_relays(eid, ev)
    print("🗑️ deletion request published (relays/clients may or may not remove the target)")



async def do_search_user():
    print_header("SEARCH USER")
    print("1) Search by pubkey (hex or npub1...)")
    print("2) Search by name (name/display_name/nip05)")
    print("0) Back")

    choice = input("\nSelect: ").strip()
    if choice == "0":
        return

    from .profile_search import fetch_profile_by_pubkey, search_profiles_by_name

    if choice == "1":
        pk = input("Enter pubkey (64-hex or npub1...): ").strip()
        try:
            prof = await fetch_profile_by_pubkey(pk)
        except ValueError as e:
            print(f"⚠️ {e}")
            return

        if not prof:
            print("No profile (kind:0) found on our relays.")
            return

        print("\nProfile:")
        print(" pubkey:", prof["_pubkey"][:12] + "…")
        print(" name:", prof.get("name") or "")
        print(" display_name:", prof.get("display_name") or "")
        print(" nip05:", prof.get("nip05") or "")
        print(" lud16:", prof.get("lud16") or "")
        print(" about:", (prof.get("about") or "")[:200])

    elif choice == "2":
        q = input("Enter name to search: ").strip()
        results = await search_profiles_by_name(q, since_days=365, per_relay_limit=800)

        if not results:
            print("No matches found (name search is relay-dependent).")
            return

        print(f"\nMatches ({len(results)}):")
        for i, prof in enumerate(results[:20], 1):
            name = prof.get("display_name") or prof.get("name") or ""
            nip05 = prof.get("nip05") or ""
            pk = prof.get("_pubkey", "")
            print(f"{i}) {name}  {nip05}  {pk[:12]}…")

    else:
        print("⚠️ invalid option")




async def main():
    privkey = get_privkey_from_env()
    my_pubkey = pubkey_xonly_hex(privkey)

    # Initial connect-style section
    await show_status(my_pubkey)

    while True:
        print_header("MENU")
        print("1) Publish")
        print("2) Events (read following)")
        print("3) Follow")
        print("4) Unfollow")
        print("5) Status")
        print("6) Reaction (like)")
        print("7) Comment (reply by event id)")
        print("8) DM")
        print("9) Delete (by event id)")
        print("10) Search User")
        print("0) Exit")

        choice = input("\nSelect: ").strip()

        if choice == "1":
            await do_publish(privkey)
        elif choice == "2":
            await do_events(my_pubkey)
        elif choice == "3":
            await do_follow(privkey, my_pubkey)
        elif choice == "4":
            await do_unfollow(privkey, my_pubkey)
        elif choice == "5":
            await show_status(my_pubkey)
        elif choice == "6":
            await do_reaction(privkey)
        elif choice == "7":
            await do_comment(privkey)
        elif choice == "8":
            await do_dm_menu(privkey, my_pubkey)
        elif choice == "9":
            await do_delete(privkey)   
        elif choice == "10":
            await do_search_user()
        elif choice == "0":
            print("\n👋 Bye")
            return
        else:
            print("⚠️ invalid option")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Stopped")
