"""Merge duplicate WhatsApp conversations into one per customer.

Dry run by default — prints what would change and touches nothing:

    uv run python scripts/merge_whatsapp_conversations.py
    uv run python scripts/merge_whatsapp_conversations.py --org <org-uuid>

Apply (a JSON backup is written first to ./backups; each customer then merges in its
own transaction, rolled back if counts mismatch). Undo later with
scripts/rollback_chat_merge.py <backup file>:

    uv run python scripts/merge_whatsapp_conversations.py --apply

Reads DATABASE_URL via the app's normal settings (.env.local, else .env), so
check which database that is before using --apply. Voice conversations are
never touched. Safe to re-run.
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from pathlib import Path

from apps.api.core.chat_backup import write_backup
from apps.api.core.chat_merge import find_duplicate_groups, merge_customer
from apps.api.db.session import AsyncSessionLocal, engine


async def main(apply: bool, org_id: UUID | None, backup_dir: Path, no_backup: bool) -> None:
    host = engine.url.host
    print(f"database host: {host}  mode: {'APPLY' if apply else 'dry run'}")
    async with AsyncSessionLocal() as db:
        groups = await find_duplicate_groups(db, org_id)
    total_rows = sum(n for _, _, n in groups)
    print(
        f"customers with duplicate WhatsApp conversations: {len(groups)} "
        f"({total_rows} rows -> {len(groups)} rows, {total_rows - len(groups)} removable)"
    )
    for _, user_id, n in groups[:10]:
        print(f"  e.g. user {user_id}: {n} conversations")
    if not apply or not groups:
        if not apply and groups:
            print("dry run only — re-run with --apply to merge")
        await engine.dispose()
        return

    if no_backup:
        print("WARNING: --no-backup given, nothing will be saved for rollback")
    else:
        async with AsyncSessionLocal() as db:
            info = await write_backup(db, [u for _, u, _ in groups], "merge-whatsapp", backup_dir)
        print(f"backup saved: {info.path} ({info.total_rows} rows)")
        print(f"  undo with: PYTHONPATH=. uv run python scripts/rollback_chat_merge.py {info.path} --apply")

    merged = failed = moved = 0
    for group_org, user_id, _ in groups:
        async with AsyncSessionLocal() as db:
            try:
                result = await merge_customer(db, group_org, user_id)
                await db.commit()
            except Exception as exc:  # noqa: BLE001 — one bad customer must not stop the rest
                await db.rollback()
                failed += 1
                print(f"  FAILED user {user_id}: {exc}")
                continue
        if result is not None:
            merged += 1
            moved += result.messages_moved
    print(f"done: merged {merged} customers, moved {moved} messages, failed {failed}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually merge (default is a dry run)")
    parser.add_argument("--org", type=UUID, default=None, help="limit to one org id")
    parser.add_argument("--backup-dir", type=Path, default=Path("backups"), help="where the pre-merge snapshot goes")
    parser.add_argument("--no-backup", action="store_true", help="skip the snapshot (not recommended)")
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.org, args.backup_dir, args.no_backup))
