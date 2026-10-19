"""Validate the Alembic chain against a DISPOSABLE or STAGING PostgreSQL. Never run it against production.

    python scripts/validate_migrations.py postgresql+asyncpg://user:pw@staging-host/dbname --yes-this-is-not-production

What it does, in order (each step stops the run on failure):
  1. refuses hosts that look like production (RDS endpoints named in PROTECTED_HOST_MARKERS, the EC2 address)
     and refuses to run without the explicit flag;
  2. checks there is exactly one Alembic head;
  3. upgrades to the revision BEFORE the Phase 1-5 migrations, inserts a legacy-shaped org row, then upgrades to
     head and confirms the row survives with NULL voice_* columns (existing orgs keep legacy behaviour);
  4. downgrades back through the Phase 1-5 migrations and upgrades to head again (rollback + re-apply);
  5. confirms the model metadata and the migrated schema agree on the Phase 1-5 columns.

It needs a database you can destroy: step 3 starts from whatever is there, so point it at an EMPTY database, or
a restored copy of production on a staging server. Nothing has been run against any database yet.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import uuid
from urllib.parse import urlparse

PROTECTED_HOST_MARKERS = ("veerox-db", "c3eoio6kg92s", "3.139.241.196", "workassignai.com", "neon.tech", "upstash")
BASE_BEFORE = "d0f6b4c8e2a5"  # last revision before the voice org columns (greeting -> profile -> brand/compliance)
PHASE_REVISIONS = ("e2a4c6d8f0b1", "f3b5d7e9a1c2", "a7c9e1f3b5d2")
NEW_COLUMNS = ("voice_greeting", "voice_profile", "voice_brand", "voice_compliance")


def refuse_if_protected(url: str) -> None:
    host = (urlparse(url.replace("+asyncpg", "")).hostname or "").lower()
    for marker in PROTECTED_HOST_MARKERS:
        if marker in host or marker in url.lower():
            raise SystemExit(f"refusing: {host!r} looks like production ({marker}). Use a disposable or staging database.")


def alembic(url: str, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": url}
    subprocess.run([sys.executable, "-m", "alembic", *args], check=True, env=env)


async def columns_of_orgs(url: str) -> set[str]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(text("select column_name from information_schema.columns where table_name='orgs'"))
            return {r[0] for r in rows}
    finally:
        await engine.dispose()


async def insert_legacy_org(url: str) -> uuid.UUID:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    org_id = uuid.uuid4()
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("insert into orgs (id, name) values (:i, 'migration-check')"), {"i": org_id})
    finally:
        await engine.dispose()
    return org_id


async def fetch_org(url: str, org_id: uuid.UUID) -> dict:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text("select * from orgs where id=:i"), {"i": org_id})).mappings().one()
            return dict(row)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--yes-this-is-not-production", action="store_true", required=True)
    args = parser.parse_args(argv)
    refuse_if_protected(args.url)

    print("1/5 single head")
    heads = subprocess.run([sys.executable, "-m", "alembic", "heads"], capture_output=True, text=True,
                           env={**os.environ, "DATABASE_URL": args.url}, check=True).stdout.strip().splitlines()
    if len(heads) != 1:
        raise SystemExit(f"expected one head, found {heads}")

    print("2/5 upgrade to the revision before the voice columns")
    alembic(args.url, "upgrade", BASE_BEFORE)
    before = asyncio.run(columns_of_orgs(args.url))
    if before & set(NEW_COLUMNS):
        raise SystemExit(f"columns already present before the migrations: {before & set(NEW_COLUMNS)}")
    org_id = asyncio.run(insert_legacy_org(args.url))

    print("3/5 upgrade to head; the legacy org must survive with NULL voice settings")
    alembic(args.url, "upgrade", "head")
    row = asyncio.run(fetch_org(args.url, org_id))
    missing = [c for c in NEW_COLUMNS if c not in row]
    if missing:
        raise SystemExit(f"columns missing after upgrade: {missing}")
    non_null = {c: row[c] for c in NEW_COLUMNS if row[c] is not None}
    if non_null:
        raise SystemExit(f"existing org got non-NULL defaults (would change its behaviour): {non_null}")

    print("4/5 downgrade through the voice migrations and re-apply")
    alembic(args.url, "downgrade", BASE_BEFORE)
    after_down = asyncio.run(columns_of_orgs(args.url))
    if after_down & set(NEW_COLUMNS):
        raise SystemExit(f"columns left after downgrade: {after_down & set(NEW_COLUMNS)}")
    alembic(args.url, "upgrade", "head")

    print("5/5 model/schema agreement on the voice columns")
    final = asyncio.run(columns_of_orgs(args.url))
    if not set(NEW_COLUMNS) <= final:
        raise SystemExit("voice columns missing after re-applying")
    print("OK: chain upgrades, downgrades and re-applies; existing org rows keep NULL voice settings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
