"""Merge contacts stored twice (``+919179609988`` and ``919179609988``) into one.

That split is why one person can show up as two different chats. Dry run by
default — prints what would change and touches nothing:

    PYTHONPATH=. uv run python scripts/merge_duplicate_contacts.py
    PYTHONPATH=. uv run python scripts/merge_duplicate_contacts.py --org <org-uuid>

Apply (each person in its own transaction, rolled back if any message or
conversation would go missing):

    PYTHONPATH=. uv run python scripts/merge_duplicate_contacts.py --apply

Run scripts/merge_whatsapp_conversations.py afterwards to also fold each
person's WhatsApp conversations into one. Reads DATABASE_URL via the app's
normal settings (.env.local, else .env) — check the host it prints first.
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from apps.api.core.user_merge import find_duplicate_contacts, merge_contact
from apps.api.db.session import AsyncSessionLocal, engine


async def main(apply: bool, org_id: UUID | None) -> None:
    print(f"database host: {engine.url.host}  mode: {'APPLY' if apply else 'dry run'}")
    async with AsyncSessionLocal() as db:
        groups = await find_duplicate_contacts(db, org_id)
    print(f"numbers stored as more than one contact: {len(groups)}")
    for org, digits, n in groups[:15]:
        print(f"  org {str(org)[:8]} ...{digits[-4:]}: {n} contacts")
    if not apply or not groups:
        if groups and not apply:
            print("dry run only — re-run with --apply to merge")
        await engine.dispose()
        return

    merged = failed = 0
    for org, digits, _ in groups:
        async with AsyncSessionLocal() as db:
            try:
                result = await merge_contact(db, org, digits)
                await db.commit()
            except Exception as exc:  # noqa: BLE001 — one bad contact must not stop the rest
                await db.rollback()
                failed += 1
                print(f"  FAILED ...{digits[-4:]}: {exc}")
                continue
        if result is not None:
            merged += 1
            print(f"  merged ...{digits[-4:]}: {result.messages} messages, {result.conversations} conversations kept")
    print(f"done: merged {merged}, failed {failed}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually merge (default is a dry run)")
    parser.add_argument("--org", type=UUID, default=None, help="limit to one org id")
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.org))
