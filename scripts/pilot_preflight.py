r"""Pilot pre-flight. Run it BEFORE any migration, seed or API start.

    python scripts\pilot_preflight.py .env.pilot                   # offline: reads two local files, no network
    python scripts\pilot_preflight.py .env.pilot --connect         # also a READ-ONLY look at the two remote services

Offline mode compares the pilot file with the main ``.env`` (default ``.env``) and fails if the database or Redis
is the same one, if the database is not the declared pilot database, if a known production host appears, or if
a risky setting is on. It never prints a secret.

``--connect`` then opens one connection to the pilot database and one to the pilot Redis and only reads:
``SELECT current_database()``, a count of rows in ``orgs`` if that table exists, ``PING`` and ``DBSIZE``. It
refuses to continue (exit 1) when the connected database is not ``PILOT_DATABASE_NAME``, when it already holds
more than the pilot org, or when Redis already holds keys (a dedicated pilot instance should be empty; a shared
one is not isolated and the key space is global). Nothing is created, changed or deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api.pilot_checks import ACK_NAME, ACK_VALUE, check_pilot_env, parse_env, summarize  # noqa: E402


async def connect_checks(pilot: dict[str, str], pilot_org_id: str) -> list[tuple[str, str, str]]:
    shared = pilot.get(ACK_NAME, "") == ACK_VALUE
    bad = "WARN" if shared else "FAIL"  # an acknowledged shared store is reported, not refused
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    results: list[tuple[str, str, str]] = []
    engine = create_async_engine(pilot["DATABASE_URL"])
    try:
        async with engine.connect() as conn:
            current = (await conn.execute(text("select current_database()"))).scalar_one()
            ok = current.lower() == pilot.get("PILOT_DATABASE_NAME", "").lower()
            results.append(("PASS" if ok else "FAIL", "connected database is the pilot database", "name matches PILOT_DATABASE_NAME" if ok else "the server reports a different database name"))
            exists = (await conn.execute(text("select to_regclass('public.orgs') is not null"))).scalar_one()
            if exists:
                rows = (await conn.execute(text("select id::text from orgs"))).scalars().all()
                foreign = [r for r in rows if r != pilot_org_id]
                results.append(("PASS" if not foreign else bad, "database holds only the pilot org", f"{len(rows)} org(s); {len(foreign)} other than DEFAULT_ORG_ID (is this a real database?)"))
                # Read-only questions that decide whether the first call can use PLIVO_PHONE_NUMBER and is not recorded.
                import re as _re

                line = _re.sub(r"\D", "", pilot.get("PLIVO_PHONE_NUMBER", ""))
                if line:
                    owners = (await conn.execute(text("select org_id::text from org_phone_numbers where provider = 'plivo' and phone_number = :n"), {"n": line})).scalars().all()
                    if not owners:
                        results.append(("PASS", "Plivo line is free for the pilot org", "no org owns it yet: startup will assign it to DEFAULT_ORG_ID"))
                    elif owners == [pilot_org_id]:
                        results.append(("PASS", "Plivo line belongs to the pilot org", "outbound calls will use it as caller ID"))
                    else:
                        results.append((bad, "Plivo line belongs to ANOTHER org", "outbound calls from the pilot org cannot use it as caller ID, and inbound calls to it resolve to that other org: give the pilot org its own number"))
                mode = (await conn.execute(text("select voice_compliance->>'recording_mode' from orgs where id::text = :i"), {"i": pilot_org_id})).scalar_one_or_none()
                if mode in (None, "off"):
                    results.append(("PASS", "pilot org does not record calls", "no policy or recording_mode=off (pilot mode also defaults to no recording)"))
                else:
                    results.append(("WARN", "pilot org has an explicit recording policy", f"recording_mode={mode}: set it to off before the first call"))
            else:
                results.append(("PASS", "database is empty of app tables", "no orgs table yet: run the migrations after approval"))
    except Exception as exc:  # noqa: BLE001 - report the class, never the message (it can contain the URL)
        results.append(("FAIL", "database reachable", f"could not connect ({type(exc).__name__})"))
    finally:
        await engine.dispose()

    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(pilot["REDIS_URL"], socket_connect_timeout=5)
        try:
            await client.ping()
            size = await client.dbsize()
            results.append(("PASS" if size == 0 else bad, "redis is a dedicated, empty instance", f"{size} key(s) already present" if size else "empty"))
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        results.append(("FAIL", "redis reachable", f"could not connect ({type(exc).__name__})"))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pilot_env")
    parser.add_argument("--main-env", default=".env")
    parser.add_argument("--connect", action="store_true", help="also do a read-only check of the two remote services")
    args = parser.parse_args(argv)

    pilot = parse_env(Path(args.pilot_env).read_text(encoding="utf8"))
    main_path = Path(args.main_env)
    main_env = parse_env(main_path.read_text(encoding="utf8")) if main_path.exists() else {}
    if not main_env:
        print(f"note: {args.main_env} not found or empty, so 'differs from main' checks cannot run", file=sys.stderr)

    rows = [(r.status, r.check, r.detail if r.status != "PASS" else "") for r in check_pilot_env(pilot, main_env)]
    import os

    effective = Path(os.environ.get("VEEROX_ENV_FILE", "")).resolve() if os.environ.get("VEEROX_ENV_FILE") else None
    rows.append((
        "PASS" if effective and effective == Path(args.pilot_env).resolve() else "FAIL",
        "the API will load ONLY the pilot file",
        "set $env:VEEROX_ENV_FILE to the pilot file in this shell; otherwise the real .env is read as a fallback for every variable the pilot file leaves out",
    ))
    leaked = sorted(k for k in pilot if k in os.environ and os.environ[k] != pilot[k])
    leaked += sorted(k for k in main_env if k in os.environ and k not in pilot and k.upper().endswith(("KEY", "TOKEN", "SECRET", "SID", "URL", "DSN")))
    rows.append(("PASS" if not leaked else "WARN", "shell environment does not override the pilot file", ("set in this shell: " + ", ".join(leaked[:8])) if leaked else ""))
    if args.connect and not any(s == "FAIL" for s, _, _ in rows):
        rows += asyncio.run(connect_checks(pilot, pilot.get("DEFAULT_ORG_ID", "00000000-0000-0000-0000-000000000001")))
    elif args.connect:
        rows.append(("FAIL", "connect skipped", "fix the offline failures first: nothing was contacted"))
    for status, check, detail in rows:
        print(f"{status:5} {check}" + (f"  -- {detail}" if detail else ""))
    failed = sum(s == "FAIL" for s, _, _ in rows)
    print(f"\n{sum(s == 'PASS' for s, _, _ in rows)} passed, {sum(s == 'WARN' for s, _, _ in rows)} warnings, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
