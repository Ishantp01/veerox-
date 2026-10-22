r"""READ-ONLY verification of the pilot's remote PostgreSQL and Redis. Prints findings; changes nothing.

    python scripts\pilot_verify_remote.py .env.pilot --i-understand-this-is-read-only

Guarantees, enforced in this file rather than promised:

* PostgreSQL: every statement must start with SELECT or SHOW (``ro_sql`` refuses anything else, including a second
  statement after a semicolon), and the transaction is put in ``READ ONLY`` mode first and ``SHOW
  transaction_read_only`` must answer "on" or the run stops. The transaction is rolled back at the end.
* Redis: only the commands in ``REDIS_READ_COMMANDS`` can be sent (no SET, DEL, LPOP, FLUSH*, EVAL...). Keys are
  counted by namespace and never printed; session tokens are never read.
* Nothing is printed that identifies a person: no URLs, passwords, phone numbers, org names or message text.
  Organisations appear only as counts, and an organisation id only as its first 8 characters.
* No provider (Plivo, OpenAI, ElevenLabs, Meta) is contacted, and no migration is run.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api.pilot_checks import ACK_NAME, ACK_VALUE, parse_env  # noqa: E402

REDIS_READ_COMMANDS = frozenset({"PING", "DBSIZE", "SCAN", "EXISTS", "GET", "LLEN", "INFO", "TYPE"})
# GET is only ever used on these two non-secret keys.
REDIS_GET_ALLOWED_KEYS = frozenset({"veerox:kill_switch"})
_ERROR_COUNTER_PREFIX = "veerox:errors:"


class NotReadOnly(Exception):
    pass


def ro_sql(sql: str) -> str:
    """Return ``sql`` if it is a single SELECT/SHOW statement, else raise."""
    statement = sql.strip().rstrip(";").strip()
    if ";" in statement:
        raise NotReadOnly("more than one statement")
    if not re.match(r"(?is)^(select|show)\b", statement):
        raise NotReadOnly("only SELECT and SHOW are allowed")
    if re.search(r"(?is)\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|vacuum|call|do)\b", re.sub(r"'[^']*'", "''", statement)):
        raise NotReadOnly("a write keyword appears outside a string")
    return statement


class ReadOnlyRedis:
    """A thin wrapper that refuses any command that is not on the read-only list."""

    def __init__(self, client):
        self._c = client
        self.sent: list[str] = []

    async def cmd(self, *args):
        name = str(args[0]).upper()
        if name not in REDIS_READ_COMMANDS:
            raise NotReadOnly(f"redis command {name} is not on the read-only list")
        if name == "GET" and str(args[1]) not in REDIS_GET_ALLOWED_KEYS:
            raise NotReadOnly("GET is only allowed on the kill-switch key")
        self.sent.append(name)
        return await self._c.execute_command(*args)


def short(value: str) -> str:
    return value[:8]


async def verify_postgres(pilot: dict[str, str], out: list[tuple[str, str]]) -> dict:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    facts: dict = {}
    engine = create_async_engine(pilot["DATABASE_URL"], pool_size=1, max_overflow=0, connect_args={"statement_cache_size": 0})
    default_org = pilot.get("DEFAULT_ORG_ID", "00000000-0000-0000-0000-000000000001")
    line_digits = re.sub(r"\D", "", pilot.get("PLIVO_PHONE_NUMBER", ""))
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))  # first statement of the transaction
            mode = (await conn.execute(text(ro_sql("show transaction_read_only")))).scalar_one()
            facts["read_only_enforced"] = mode == "on"
            if mode != "on":
                raise NotReadOnly("the server did not accept READ ONLY mode")

            async def one(sql: str, **params):
                return (await conn.execute(text(ro_sql(sql)), params)).scalar_one_or_none()

            async def rows(sql: str, **params):
                return (await conn.execute(text(ro_sql(sql)), params)).all()

            facts["database"] = await one("select current_database()")
            facts["role_is_superuser"] = await one("select rolsuper from pg_roles where rolname = current_user")
            facts["database_name_matches_PILOT_DATABASE_NAME"] = str(facts["database"]).lower() == pilot.get("PILOT_DATABASE_NAME", "").lower()

            present = {r[0] for r in await rows("select table_name from information_schema.tables where table_schema = 'public'")}
            with_created = {r[0] for r in await rows("select table_name from information_schema.columns where table_schema = 'public' and column_name = 'created_at'")}
            conv_columns = {x[0] for x in await rows("select column_name from information_schema.columns where table_schema = 'public' and table_name = 'conversations'")}
            conv_time = next((c for c in ("created_at", "started_at", "updated_at") if c in conv_columns), None)
            facts["tables"] = len(present)
            facts["alembic"] = await one("select version_num from alembic_version") if "alembic_version" in present else "none (schema not created yet)"
            now = datetime.now(UTC)
            activity: dict[str, dict] = {}
            for table in ("orgs", "users", "conversations", "messages", "leads", "appointments", "usage_events", "org_phone_numbers"):
                if table not in present:
                    continue
                count = await one(f"select count(*) from {table}")
                info: dict = {"rows": count}
                if table in with_created:
                    latest = await one(f"select max(created_at) from {table}")
                    if latest is not None:
                        latest = latest if latest.tzinfo else latest.replace(tzinfo=UTC)
                        info["newest_days_ago"] = round((now - latest).total_seconds() / 86400, 1)
                    for days in (1, 7, 30):
                        info[f"last_{days}d"] = await one(f"select count(*) from {table} where created_at > :t", t=now - timedelta(days=days))
                activity[table] = info
            facts["activity"] = activity

            sessions = await rows("select coalesce(nullif(application_name, ''), '(none)'), state, count(*) from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid() group by 1, 2")
            facts["other_sessions"] = sum(r[2] for r in sessions)
            facts["other_session_apps"] = sorted({r[0][:24] for r in sessions})

            if "orgs" in present:
                facts["orgs"] = await one("select count(*) from orgs")
                facts["default_org_exists"] = bool(await one("select count(*) from orgs where id::text = :i", i=default_org))
                facts["orgs_named_like_test"] = await one("select count(*) from orgs where name ~* '(test|pilot|demo|sandbox)'")
                facts["default_org_is_seeded_demo"] = bool(await one("select count(*) from orgs where id::text = :i and name = 'Demo Org'", i=default_org))
                if "conversations" in present and conv_time:
                    facts["orgs_with_conversations_last_30d"] = await one(f"select count(distinct org_id) from conversations where {conv_time} > :t", t=now - timedelta(days=30))
                    newest = await one(f"select max({conv_time}) from conversations")
                    facts["conversations_newest_days_ago"] = round((now - newest.replace(tzinfo=UTC)).total_seconds() / 86400, 1) if newest else "none (no conversations)"
                if "conversations" in present:
                    facts["default_org_conversations"] = await one("select count(*) from conversations where org_id::text = :i", i=default_org)
                if "leads" in present:
                    facts["default_org_leads"] = await one("select count(*) from leads where org_id::text = :i", i=default_org)
                if "users" in present:
                    facts["default_org_users"] = await one("select count(*) from users where org_id::text = :i", i=default_org)
                mode_row = await rows("select voice_compliance is null, voice_compliance->>'recording_mode' from orgs where id::text = :i", i=default_org) if "voice_compliance" in {r[0] for r in await rows("select column_name from information_schema.columns where table_name = 'orgs'")} else []
                facts["default_org_recording"] = ("no policy" if mode_row and mode_row[0][0] else (mode_row[0][1] if mode_row else "unknown (org or column missing)"))
            if "org_phone_numbers" in present and line_digits:
                owners = await rows("select org_id::text from org_phone_numbers where provider = 'plivo' and phone_number = :n", n=line_digits)
                facts["plivo_line_owners"] = len(owners)
                facts["plivo_line_owned_by_default_org"] = any(o[0] == default_org for o in owners)
                facts["plivo_line_owner_prefixes"] = sorted(short(o[0]) for o in owners)
                if owners:
                    other = [o[0] for o in owners if o[0] != default_org]
                    if other:
                        if "conversations" in present and conv_time:
                            facts["owner_org_conversations_last_30d"] = await one(f"select count(*) from conversations where org_id::text = :o and {conv_time} > :t", o=other[0], t=now - timedelta(days=30))
            await conn.rollback()
    except NotReadOnly as exc:
        out.append(("BLOCKED", f"read-only mode could not be guaranteed ({exc})"))
    except Exception as exc:  # noqa: BLE001 - report the class only; the message can contain the URL
        out.append(("BLOCKED", f"PostgreSQL could not be read ({type(exc).__name__})"))
    finally:
        await engine.dispose()
    return facts


async def verify_redis(pilot: dict[str, str], out: list[tuple[str, str]]) -> dict:
    import redis.asyncio as aioredis

    facts: dict = {}
    client = aioredis.from_url(pilot["REDIS_URL"], socket_connect_timeout=5, socket_timeout=8, decode_responses=True)
    r = ReadOnlyRedis(client)
    try:
        facts["ping"] = await r.cmd("PING")
        facts["dbsize"] = await r.cmd("DBSIZE")
        for section in ("clients", "stats"):
            try:
                info = await r.cmd("INFO", section)
                if isinstance(info, str):  # a raw reply: turn "key:value" lines into a dict
                    info = dict(line.split(":", 1) for line in info.splitlines() if ":" in line)
                for key in ("connected_clients", "instantaneous_ops_per_sec", "total_commands_processed"):
                    if key in info:
                        facts[key] = int(info[key])
            except Exception as exc:  # noqa: BLE001
                facts[f"info_{section}"] = f"unsupported ({type(exc).__name__})"
        namespaces: dict[str, int] = {}
        cursor, scanned = "0", 0
        while scanned < 20000:
            cursor, keys = await r.cmd("SCAN", cursor, "COUNT", 500)
            for k in keys:
                ns = k.split(":", 1)[0]
                ns = ns if re.fullmatch(r"[a-z_]{1,24}", ns) else "<other>"
                namespaces[ns] = namespaces.get(ns, 0) + 1
            scanned += len(keys)
            if str(cursor) == "0":
                break
        facts["keys_scanned"] = scanned
        facts["scan_complete"] = str(cursor) == "0"
        facts["namespaces"] = dict(sorted(namespaces.items(), key=lambda kv: -kv[1])[:8])
        facts["kill_switch_key_exists"] = bool(await r.cmd("EXISTS", "veerox:kill_switch"))
        facts["kill_switch_engaged"] = (await r.cmd("GET", "veerox:kill_switch")) is not None
        facts["handoff_queue_length"] = await r.cmd("LLEN", "human_handoff_queue")
        today = datetime.now(UTC).date()
        found = 0
        for d in range(3):
            found += bool(await r.cmd("EXISTS", f"{_ERROR_COUNTER_PREFIX}{(today - timedelta(days=d)).isoformat()}"))
        facts["error_counters_last_3_days"] = found
        if "total_commands_processed" in facts:
            before, sent_before = facts["total_commands_processed"], len(r.sent)
            await asyncio.sleep(10)
            after = await r.cmd("INFO", "stats")
            if isinstance(after, dict) and "total_commands_processed" in after:
                mine = len(r.sent) - sent_before  # everything this script sent since the first reading, incl. this INFO
                facts["commands_by_others_in_10s"] = max(0, int(after["total_commands_processed"]) - before - mine)
        facts["commands_sent_by_this_script"] = sorted(set(r.sent))
    except NotReadOnly as exc:
        out.append(("BLOCKED", f"redis read-only list violated ({exc})"))
    except Exception as exc:  # noqa: BLE001
        out.append(("BLOCKED", f"Redis could not be read ({type(exc).__name__})"))
    finally:
        await client.aclose()
    return facts


def show(title: str, facts: dict) -> None:
    print(f"\n== {title}")
    for key, value in facts.items():
        if isinstance(value, dict):
            print(f"  {key}:")
            for k, v in value.items():
                print(f"      {k}: {v}")
        else:
            print(f"  {key}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pilot_env")
    parser.add_argument("--i-understand-this-is-read-only", action="store_true", required=True)
    parser.add_argument("--skip-redis", action="store_true")
    parser.add_argument("--skip-postgres", action="store_true")
    args = parser.parse_args(argv)
    pilot = parse_env(Path(args.pilot_env).read_text(encoding="utf8"))
    problems: list[tuple[str, str]] = []
    print(f"shared-data acknowledgement present in the file: {pilot.get(ACK_NAME, '') == ACK_VALUE} (this script neither sets nor needs it)")
    if not args.skip_postgres:
        show("PostgreSQL (READ ONLY transaction)", asyncio.run(verify_postgres(pilot, problems)))
    if not args.skip_redis:
        show("Redis (read-only command list)", asyncio.run(verify_redis(pilot, problems)))
    for status, detail in problems:
        print(f"\n{status}: {detail}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
