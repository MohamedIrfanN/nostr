import asyncio
from .lookup import fetch_event_by_id_all_relays
from .config import RELAYS
from .utils import get_privkey_from_env, pubkey_xonly_hex
from .events import build_signed_text_note, build_signed_contacts_event
from .publish import publish_to_relays
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
    print("Relays:")
    for r in RELAYS:
        print(" -", r)

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
    pk = input("Enter pubkey (64-hex): ").strip().lower()

    current = await fetch_following_all_relays(my_pubkey)
    updated = apply_follow(current, pk)

    # publish updated contacts (kind:3)
    eid, ev = build_signed_contacts_event(privkey, sorted(updated))
    await publish_to_relays(eid, ev)
    print(f"✅ Now following: {len(updated)}")


async def do_unfollow(privkey, my_pubkey: str):
    print_header("UNFOLLOW")
    pk = input("Enter pubkey (64-hex): ").strip().lower()

    current = await fetch_following_all_relays(my_pubkey)
    updated = apply_unfollow(current, pk)

    eid, ev = build_signed_contacts_event(privkey, sorted(updated))
    await publish_to_relays(eid, ev)
    print(f"✅ Now following: {len(updated)}")


async def do_reaction(privkey):
    print_header("REACTION (LIKE)")
    target_id = input("Enter event id (64-hex): ").strip().lower()

    # Try to lookup the target event to get author pubkey for ["p", ...]
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

    # Build + publish reaction
    from .events import build_signed_reaction
    eid, reaction_event = build_signed_reaction(
        privkey,
        target_event_id=target_id,
        target_pubkey=target_pubkey,
        reaction="+",  # like
    )
    await publish_to_relays(eid, reaction_event)



async def do_comment(privkey):
    print_header("COMMENT")
    target_id = input("Enter event id (64-hex): ").strip().lower()
    comment_text = input("Enter comment text: ").strip()

    # Lookup the target event first (so we can tag root/reply/p correctly)
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
    recipient = input("Enter recipient pubkey (64-hex or npub1...): ").strip()
    msg = input("Enter message: ").strip()

    from .events import build_signed_dm
    eid, ev = build_signed_dm(privkey, recipient, msg)
    await publish_to_relays(eid, ev)





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
        print("8) DM (send)")
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
            await do_dm(privkey)
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
