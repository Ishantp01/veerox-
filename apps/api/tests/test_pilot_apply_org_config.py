"""Rehearsal of scripts/pilot_apply_org_config.py against the in-memory test database, through the REAL admin and
conversation-engine endpoints. The real pilot database is never touched here.

It proves the apply sequence is accepted end to end, that the voice pipeline loads what was stored, that the refusal
rules hold, that nothing leaks across organizations, and that the rollback restores the previous behavior.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import realtime_bridge as bridge
from apps.api.core.prompts import OUTBOUND_CALL_PROMPT
from apps.api.db.models import Org, Script
from apps.api.tests.test_admin_endpoints import ADMIN_HEADERS, ORG_ID, _seed_org, client, fake_redis  # noqa: F401

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("pilot_apply_org_config", ROOT / "scripts" / "pilot_apply_org_config.py")
applier = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["pilot_apply_org_config"] = applier
_spec.loader.exec_module(applier)  # type: ignore[union-attr]

CFG = applier.load_preview()


@pytest.fixture
def reuse_db(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    class _Ctx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(bridge, "AsyncSessionLocal", lambda: _Ctx())
    return db_session


async def _pilot_org(db: AsyncSession) -> Org:
    org = await _seed_org(db)
    org.name = "Veerox Pilot"
    org.voice_compliance = {"recording_mode": "off"}
    await db.commit()
    return org


# --- the apply sequence ---------------------------------------------------------------------------------------------------------


async def test_the_apply_sequence_is_accepted_by_every_real_endpoint_and_reads_back_exactly(client: AsyncClient, db_session: AsyncSession) -> None:
    await _pilot_org(db_session)
    done = await applier.apply_through(client, ADMIN_HEADERS, CFG)
    assert len(done) == 2 + len(CFG["knowledge"]["items"]) and all(status in (200, 201) for _, status in done)
    checks = await applier.read_back(client, ADMIN_HEADERS, CFG)
    assert checks and all(checks.values()), checks


async def test_the_voice_pipeline_loads_the_stored_script_and_greeting(client: AsyncClient, reuse_db: AsyncSession) -> None:
    org = await _pilot_org(reuse_db)
    before = await bridge._system_instructions(None, org.id, None)
    assert before.startswith(OUTBOUND_CALL_PROMPT.strip()) and await bridge._load_org_greeting(org.id) is None  # flag-off: today's behavior

    await applier.apply_through(client, ADMIN_HEADERS, CFG)
    after = await bridge._system_instructions(None, org.id, None)
    assert after.startswith(CFG["script"]["body"]["content"].strip())
    assert "AI assistant" in after and len(after) > len(CFG["script"]["body"]["content"])  # the platform rules still follow it
    assert await bridge._load_org_greeting(org.id) == CFG["greeting"]["body"]["greeting"]


async def test_a_greeting_that_hides_the_ai_is_still_refused_by_the_real_endpoint(client: AsyncClient, db_session: AsyncSession) -> None:
    await _pilot_org(db_session)
    r = await client.put("/admin/settings/voice-greeting", json={"greeting": "Namaste, Veerox se baat kar rahe hain."}, headers=ADMIN_HEADERS)
    assert r.status_code == 422


async def test_applying_never_changes_recording_or_creates_customer_rows(client: AsyncClient, db_session: AsyncSession) -> None:
    from sqlalchemy import func, select

    from apps.api.db.models import Conversation, Lead, User

    org = await _pilot_org(db_session)
    await applier.apply_through(client, ADMIN_HEADERS, CFG)
    await db_session.refresh(org)
    assert org.voice_compliance == {"recording_mode": "off"} and org.name == "Veerox Pilot"
    for model in (User, Lead, Conversation):
        assert (await db_session.execute(select(func.count()).select_from(model))).scalar_one() == 0


# --- isolation --------------------------------------------------------------------------------------------------------------------


async def test_another_orgs_calls_never_see_the_pilot_script(client: AsyncClient, reuse_db: AsyncSession) -> None:
    pilot = await _pilot_org(reuse_db)
    other = Org(id=uuid.UUID("00000000-0000-0000-0000-0000000000e7"), name="Another org")
    reuse_db.add(other)
    reuse_db.add(Script(org_id=other.id, name="theirs", content="THEIR OWN SCRIPT", channel="voice", is_default=True))
    await reuse_db.commit()
    await applier.apply_through(client, ADMIN_HEADERS, CFG)
    theirs = await bridge._system_instructions(None, other.id, None)
    mine = await bridge._system_instructions(None, pilot.id, None)
    assert theirs.startswith("THEIR OWN SCRIPT") and "SYNTHETIC PILOT SCRIPT" not in theirs
    assert "THEIR OWN SCRIPT" not in mine and await bridge._load_org_greeting(other.id) is None


# --- rollback ---------------------------------------------------------------------------------------------------------------------


async def test_the_documented_rollback_restores_todays_behavior(client: AsyncClient, reuse_db: AsyncSession) -> None:
    org = await _pilot_org(reuse_db)
    await applier.apply_through(client, ADMIN_HEADERS, CFG)
    assert (await client.put("/admin/settings/voice-greeting", json={"greeting": None}, headers=ADMIN_HEADERS)).status_code == 200
    for script in (await client.get("/admin/scripts", headers=ADMIN_HEADERS)).json():
        assert (await client.delete(f"/admin/scripts/{script['id']}", headers=ADMIN_HEADERS)).status_code in (200, 204)
    for item in (await client.get("/conversation-engine/knowledge", headers=ADMIN_HEADERS)).json():
        assert (await client.delete(f"/conversation-engine/knowledge/{item['id']}", headers=ADMIN_HEADERS)).status_code == 204
    assert (await client.get("/admin/scripts", headers=ADMIN_HEADERS)).json() == []
    assert (await client.get("/conversation-engine/knowledge", headers=ADMIN_HEADERS)).json() == []
    assert (await bridge._system_instructions(None, org.id, None)).startswith(OUTBOUND_CALL_PROMPT.strip())
    assert await bridge._load_org_greeting(org.id) is None


# --- the refusal rules ------------------------------------------------------------------------------------------------------------


def _good(**changes: Any) -> tuple[dict[str, Any], Any]:
    facts = {
        "database": "neondb", "orgs": [("00000000-0000-0000-0000-000000000001", "Veerox Pilot", "off")],
        "foreign_phones": 0, "counts": {"scripts": 0, "knowledge_items": 0, "users": 2, "leads": 2, "conversations": 2, "messages": 50},
    }
    facts.update({k: v for k, v in changes.items() if k in facts})
    settings = SimpleNamespace(
        environment="pilot", pilot_database_name="neondb", default_org_id="00000000-0000-0000-0000-000000000001",
        pilot_allowed_numbers="+911234567890",
    )
    return facts, settings


def _refusals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, facts: dict[str, Any], settings: Any) -> list[str]:
    pilot_file, main_file = tmp_path / "pilot.env", tmp_path / "main.env"
    main_file.write_text("DATABASE_URL=postgresql+asyncpg://u:p@main.example.net/x\nREDIS_URL=rediss://d:p@mr.example.net:6379\nPUBLIC_BASE_URL=https://m.ngrok-free.dev\n")
    pilot_file.write_text(
        "ENVIRONMENT=pilot\nPILOT_DATABASE_NAME=neondb\nDATABASE_URL=postgresql+asyncpg://u:p@new.example.net/neondb\n"
        "REDIS_URL=rediss://d:p@nr.example.net:6379\nPUBLIC_BASE_URL=https://p.ngrok-free.dev\nPILOT_ALLOWED_NUMBERS=+911234567890\n"
    )
    monkeypatch.setenv("VEEROX_ENV_FILE", str(pilot_file))
    return applier.refusals(facts, settings, pilot_file, main_file)


def test_when_everything_holds_there_is_nothing_to_refuse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _refusals(monkeypatch, tmp_path, *_good()) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"orgs": [("00000000-0000-0000-0000-000000000001", "Veerox Pilot", "off"), ("x", "Real Customer Co", "always")]}, "exactly one org"),
        ({"orgs": [("00000000-0000-0000-0000-000000000001", "Demo Org", "off")]}, "exactly one org"),
        ({"orgs": [("00000000-0000-0000-0000-000000000001", "Veerox Pilot", "always")]}, "recording is not explicitly off"),
        ({"orgs": [("00000000-0000-0000-0000-000000000001", "Veerox Pilot", "(none)")]}, "recording is not explicitly off"),
        ({"foreign_phones": 1}, "real customer data"),
        ({"database": "customers"}, "not PILOT_DATABASE_NAME"),
        ({"counts": {"scripts": 1, "knowledge_items": 0, "users": 0, "leads": 0, "conversations": 0, "messages": 0}}, "roll back first"),
        ({"counts": {"scripts": 0, "knowledge_items": 3, "users": 0, "leads": 0, "conversations": 0, "messages": 0}}, "roll back first"),
    ],
)
def test_each_unsafe_condition_stops_the_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, change: dict[str, Any], expected: str) -> None:
    facts, settings = _good(**change)
    assert any(expected in p for p in _refusals(monkeypatch, tmp_path, facts, settings))


def test_the_wrong_environment_or_an_unset_env_file_stops_the_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    facts, settings = _good()
    settings.environment = "dev"
    assert any("ENVIRONMENT must be pilot" in p for p in _refusals(monkeypatch, tmp_path, facts, settings))
    facts, settings = _good()
    monkeypatch.delenv("VEEROX_ENV_FILE", raising=False)
    pilot_file = tmp_path / "pilot.env"
    assert any("VEEROX_ENV_FILE" in p for p in applier.refusals(facts, settings, pilot_file, tmp_path / "none.env"))


def test_an_empty_allowlist_stops_the_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    facts, settings = _good()
    settings.pilot_allowed_numbers = ""
    assert any("allowlist is empty" in p for p in _refusals(monkeypatch, tmp_path, facts, settings))
