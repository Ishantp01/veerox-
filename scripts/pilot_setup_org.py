r"""Set up the isolated Pilot Org (recording explicitly off). DRY RUN by default: it prints the plan and writes nothing.

    $env:VEEROX_ENV_FILE = (Resolve-Path .env.pilot).Path
    python scripts\pilot_setup_org.py .env.pilot                                   # dry run: plan + identity checks
    python scripts\pilot_setup_org.py .env.pilot --apply --i-approve-writes-to-the-pilot-database

``--apply`` is refused unless ALL of these hold:
  * ``VEEROX_ENV_FILE`` points at the file you named, so the process uses the pilot settings and nothing from .env;
  * every offline pre-flight check passes (different database and Redis from the main .env, pilot mode on, workers
    off, allowlist set, transcripts not logged...);
  * the connected database is the declared ``PILOT_DATABASE_NAME`` and the migrations have been applied (it never
    runs them);
  * the database holds no org other than ``DEFAULT_ORG_ID`` (a real database is refused).
It then runs ``apps/api/pilot_setup.py::apply`` and reads the result back. It makes no provider request.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api.pilot_checks import check_pilot_env, parse_env  # noqa: E402
from apps.api.pilot_setup import PLAN  # noqa: E402

def expected_head() -> str:
    """The migration head in this checkout, so the script cannot go stale."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def refusals(pilot_file: Path, main_file: Path) -> list[str]:
    """Why ``--apply`` must not run (empty list = all offline conditions hold). Pure and testable."""
    problems: list[str] = []
    override = os.environ.get("VEEROX_ENV_FILE")
    if not override or Path(override).resolve() != pilot_file.resolve():
        problems.append("VEEROX_ENV_FILE must point at the pilot file in this shell")
    pilot = parse_env(pilot_file.read_text(encoding="utf8"))
    main = parse_env(main_file.read_text(encoding="utf8", errors="replace")) if main_file.exists() else {}
    problems += [f"preflight: {r.check}" for r in check_pilot_env(pilot, main) if r.status == "FAIL"]
    return problems


async def run(apply: bool) -> int:
    from sqlalchemy import text

    from apps.api.db.session import AsyncSessionLocal, engine
    from apps.api import pilot_setup
    from apps.api.config import settings

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            name = (await db.execute(text("select current_database()"))).scalar_one()
            if str(name).lower() != settings.pilot_database_name.lower():
                print("REFUSED: the connected database is not PILOT_DATABASE_NAME")
                return 1
            has_alembic = (await db.execute(text("select to_regclass('public.alembic_version') is not null"))).scalar_one()
            head = (await db.execute(text("select version_num from alembic_version"))).scalar_one_or_none() if has_alembic else None
            if head != expected_head():
                print("REFUSED: the migrations are not applied (this script never runs them); expected the latest revision")
                return 1
            orgs = (await db.execute(text("select id::text from orgs"))).scalars().all()
            if [o for o in orgs if o != settings.default_org_id]:
                print("REFUSED: the database holds an org other than DEFAULT_ORG_ID: this does not look like a fresh pilot database")
                return 1
            await db.rollback()
        if not apply:
            print("dry run OK: the database is the declared pilot database, migrated, and holds at most the pilot org.")
            return 0
        async with AsyncSessionLocal() as db:
            result = await pilot_setup.apply(db)
        async with AsyncSessionLocal() as db:
            after = await pilot_setup.describe(db)
        print("applied:", result)
        print("read back:", after)
        return 0 if after.get("recording_explicitly_off") else 1
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pilot_env")
    parser.add_argument("--main-env", default=".env")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--i-approve-writes-to-the-pilot-database", action="store_true")
    args = parser.parse_args(argv)
    print("Plan:")
    for step in PLAN:
        print("  -", step)
    problems = refusals(Path(args.pilot_env), Path(args.main_env))
    if problems:
        print("\nREFUSED (offline conditions):")
        for p in problems:
            print("  -", p)
        return 1
    if args.apply and not args.i_approve_writes_to_the_pilot_database:
        print("\nREFUSED: --apply needs --i-approve-writes-to-the-pilot-database")
        return 1
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    sys.exit(main())
