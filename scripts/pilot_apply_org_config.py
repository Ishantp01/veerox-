r"""Apply the reviewed synthetic business configuration to the isolated Veerox Pilot org, then read it back.

    $env:VEEROX_ENV_FILE = (Resolve-Path .env.pilot).Path
    python scripts\pilot_apply_org_config.py .env.pilot                                   # dry run: checks only, writes nothing
    python scripts\pilot_apply_org_config.py .env.pilot --apply --i-approve-writes-to-the-pilot-database

It calls the EXISTING admin and conversation-engine endpoints (greeting, script library, knowledge) through an in-process
ASGI client: no server is started and nothing listens on a port. It then calls the voice bridge's own
``_system_instructions`` and ``_load_org_greeting`` to prove the call pipeline would load what was stored.

Refuses to write unless ALL of these hold: ``VEEROX_ENV_FILE`` is the pilot file; ``ENVIRONMENT=pilot``; the offline
preflight has no failure; the connected database is the declared pilot database; it holds exactly one org, named
"Veerox Pilot", with recording explicitly off; every user/lead phone is on the allowlist (no real customer data); and the
org has no script or knowledge yet (so a second run cannot duplicate anything: roll back first). Prints counts and
booleans only: no numbers, tokens or business text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PREVIEW = Path(__file__).resolve().parents[1] / "docs" / "pilot-org-config.preview.json"


def load_preview(path: Path = PREVIEW) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf8"))


async def apply_through(client: Any, headers: dict[str, str], cfg: dict[str, Any]) -> list[tuple[str, int]]:
    """Greeting, then script, then knowledge, through the real endpoints. Stops at the first failure.
    Returns (step, HTTP status) for each call made."""
    done: list[tuple[str, int]] = []

    async def step(name: str, method: str, url: str, body: dict[str, Any]) -> bool:
        r = await client.request(method, url, json=body, headers=headers)
        done.append((name, r.status_code))
        return r.status_code in (200, 201)

    if not await step("greeting", "PUT", "/admin/settings/voice-greeting", cfg["greeting"]["body"]):
        return done
    if not await step("script", "POST", "/admin/scripts", cfg["script"]["body"]):
        return done
    for index, item in enumerate(cfg["knowledge"]["items"], 1):
        if not await step(f"knowledge {index}", "POST", "/conversation-engine/knowledge", item):
            return done
    return done


async def read_back(client: Any, headers: dict[str, str], cfg: dict[str, Any]) -> dict[str, bool]:
    """Compare what the endpoints now return with the preview. Booleans only."""
    out: dict[str, bool] = {}
    greeting = (await client.get("/admin/settings/voice-greeting", headers=headers)).json()
    out["greeting stored exactly"] = greeting.get("greeting") == cfg["greeting"]["body"]["greeting"]
    scripts = (await client.get("/admin/scripts", headers=headers)).json()
    want = cfg["script"]["body"]
    out["exactly one script, voice channel, default"] = len(scripts) == 1 and scripts[0].get("channel") == "voice" and scripts[0].get("is_default") is True
    out["script content stored exactly"] = bool(scripts) and scripts[0].get("content") == want["content"]
    knowledge = (await client.get("/conversation-engine/knowledge", headers=headers)).json()
    titles = {i["title"] for i in cfg["knowledge"]["items"]}
    out["all knowledge items stored"] = {k.get("title") for k in knowledge} == titles and len(knowledge) == len(titles)
    out["every knowledge item active"] = bool(knowledge) and all(k.get("active") is True for k in knowledge)
    return out


def pipeline_check_description() -> str:
    return "the voice bridge's _system_instructions and _load_org_greeting"


async def _db_facts(settings: Any) -> dict[str, Any]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0, connect_args={"statement_cache_size": 0})
    allow = {re.sub(r"\D", "", n)[-10:] for n in settings.pilot_allowed_numbers.split(",") if n.strip()}
    try:
        async with engine.connect() as c:
            await c.execute(text("SET TRANSACTION READ ONLY"))
            facts: dict[str, Any] = {}
            facts["database"] = (await c.execute(text("select current_database()"))).scalar_one()
            facts["orgs"] = (await c.execute(text("select id::text, name, coalesce(voice_compliance->>'recording_mode','(none)') from orgs"))).all()
            phones = (await c.execute(text("select phone from users union all select phone from leads"))).scalars().all()
            facts["foreign_phones"] = sum(1 for p in phones if re.sub(r"\D", "", p or "")[-10:] not in allow)
            facts["counts"] = {t: (await c.execute(text(f"select count(*) from {t}"))).scalar_one() for t in ("users", "leads", "conversations", "messages", "scripts", "knowledge_items")}
            await c.rollback()
            return facts
    finally:
        await engine.dispose()


def refusals(facts: dict[str, Any], settings: Any, pilot_file: Path, main_file: Path) -> list[str]:
    """Why the write must not happen. Pure and testable; an empty list means every precondition holds."""
    from apps.api.pilot_checks import check_pilot_env, parse_env

    problems: list[str] = []
    override = os.environ.get("VEEROX_ENV_FILE")
    if not override or Path(override).resolve() != pilot_file.resolve():
        problems.append("VEEROX_ENV_FILE must point at the pilot file")
    if settings.environment != "pilot":
        problems.append("ENVIRONMENT must be pilot")
    pilot_env = parse_env(pilot_file.read_text(encoding="utf8"))
    main_env = parse_env(main_file.read_text(encoding="utf8", errors="replace")) if main_file.exists() else {}
    problems += [f"preflight: {r.check}" for r in check_pilot_env(pilot_env, main_env) if r.status == "FAIL"]
    if str(facts["database"]).lower() != settings.pilot_database_name.lower():
        problems.append("the connected database is not PILOT_DATABASE_NAME")
    orgs = facts["orgs"]
    if len(orgs) != 1 or orgs[0][1] != "Veerox Pilot" or orgs[0][0] != settings.default_org_id:
        problems.append("the database must hold exactly one org: Veerox Pilot, with DEFAULT_ORG_ID")
    elif orgs[0][2] != "off":
        problems.append("recording is not explicitly off")
    if facts["foreign_phones"]:
        problems.append("a user or lead phone is not on the allowlist: this may be real customer data")
    if not [n for n in settings.pilot_allowed_numbers.split(",") if n.strip()]:
        problems.append("the phone allowlist is empty")
    if facts["counts"]["scripts"] or facts["counts"]["knowledge_items"]:
        problems.append("the org already has a script or knowledge: roll back first so nothing is duplicated")
    return problems


async def run(args: argparse.Namespace) -> int:
    from apps.api.config import settings

    cfg = load_preview()
    facts = await _db_facts(settings)
    problems = refusals(facts, settings, Path(args.pilot_env), Path(args.main_env))
    print("precondition counts:", facts["counts"], "| phones outside the allowlist:", facts["foreign_phones"])
    if problems:
        print("REFUSED:")
        for p in problems:
            print("  -", p)
        return 1
    print("every precondition holds.")
    if not args.apply:
        print("dry run: nothing was written. Re-run with --apply --i-approve-writes-to-the-pilot-database.")
        return 0
    if not args.i_approve_writes_to_the_pilot_database:
        print("REFUSED: --apply needs --i-approve-writes-to-the-pilot-database")
        return 1

    import httpx

    from apps.api.main import create_app

    headers = {"X-Admin-Token": settings.admin_token}
    transport = httpx.ASGITransport(app=create_app())  # in-process: no server, no port, no lifespan, no workers
    async with httpx.AsyncClient(transport=transport, base_url="http://pilot.invalid", timeout=60) as client:
        done = await apply_through(client, headers, cfg)
        for name, status in done:
            print(f"  {name:14} HTTP {status}")
        expected = 2 + len(cfg["knowledge"]["items"])
        if len(done) != expected or any(s not in (200, 201) for _, s in done):
            print("STOPPED at the first failure. Partial state is possible: use the rollback section of docs/pilot-business-config-preview.md.")
            return 1
        checks = await read_back(client, headers, cfg)

    from apps.api.channels.voice import realtime_bridge

    org_id = __import__("uuid").UUID(settings.default_org_id)
    instructions = await realtime_bridge._system_instructions(None, org_id, None)
    greeting = await realtime_bridge._load_org_greeting(org_id)
    checks["pipeline: instructions start with the stored script"] = instructions.startswith(cfg["script"]["body"]["content"].strip())
    checks["pipeline: platform rules and AI disclosure follow the script"] = "AI assistant" in instructions and len(instructions) > len(cfg["script"]["body"]["content"])
    checks["pipeline: greeting loaded exactly"] = greeting == cfg["greeting"]["body"]["greeting"]
    after = await _db_facts(settings)
    checks["still exactly one org, recording off"] = len(after["orgs"]) == 1 and after["orgs"][0][2] == "off" and after["orgs"][0][1] == "Veerox Pilot"
    unchanged = {k: after["counts"][k] == facts["counts"][k] for k in ("users", "leads", "conversations", "messages")}
    checks["no customer-side rows were created"] = all(unchanged.values()) and after["foreign_phones"] == 0
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    return 0 if all(checks.values()) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pilot_env")
    parser.add_argument("--main-env", default=".env")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--i-approve-writes-to-the-pilot-database", dest="i_approve_writes_to_the_pilot_database", action="store_true")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
