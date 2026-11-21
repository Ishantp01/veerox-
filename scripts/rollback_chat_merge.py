"""Undo a chat merge from the backup it wrote.

Dry run by default: compares the backup with the database and prints what
would be put back, changing nothing:

    PYTHONPATH=. uv run python scripts/rollback_chat_merge.py backups/merge-contacts-<time>.json

Restore (one transaction, all or nothing, verified before it commits):

    PYTHONPATH=. uv run python scripts/rollback_chat_merge.py backups/<file>.json --apply

Rows the merge deleted are re-created and rows it changed go back to their saved
values. Rows created after the backup (new messages, new chats) are left as they
are, and nothing is ever deleted. Reads DATABASE_URL via the app's normal
settings (.env.local, else .env), so check the host it prints first.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from apps.api.core.chat_backup import load_backup, plan_restore, restore_snapshot
from apps.api.db.session import AsyncSessionLocal, engine


async def main(path: Path, apply: bool) -> None:
    snapshot = load_backup(path)
    print(f"database host: {engine.url.host}  mode: {'APPLY' if apply else 'dry run'}")
    print(f"backup: {path} ({sum(len(r) for r in snapshot.values())} rows, {len(snapshot)} tables)")
    async with AsyncSessionLocal() as db:
        plan = await plan_restore(db, snapshot)
        for name in snapshot:
            m, c, s = plan.missing[name], plan.changed[name], plan.same[name]
            if m or c:
                print(f"  {name}: {m} deleted -> re-create, {c} changed -> reset, {s} already as saved")
        if not plan.needs_restore:
            print("nothing to restore: the database already matches the backup")
            await engine.dispose()
            return
        if not apply:
            print("dry run only: re-run with --apply to restore")
            await engine.dispose()
            return
        try:
            written = await restore_snapshot(db, snapshot)
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - leave the database untouched on any failure
            await db.rollback()
            print(f"FAILED, nothing was changed: {exc}")
            await engine.dispose()
            raise SystemExit(1)
    print(f"done: restored {written} rows")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("backup", type=Path, help="JSON file written by a merge script")
    parser.add_argument("--apply", action="store_true", help="actually restore (default is a dry run)")
    args = parser.parse_args()
    asyncio.run(main(args.backup, args.apply))
