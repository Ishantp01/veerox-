"""Phase 4 plumbing: the per-org opt-in setting, memory whitelisting and
isolation, decision statistics and the CRM disposition write. No model, no
network; the in-memory database only.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.conversation import builder, dialogue, engine, runtime_stats
from apps.api.core.conversation.builder import BuilderError, full_config_json
from apps.api.core.conversation.config import (
    DEFAULT_SCENARIOS,
    config_from_json,
    default_config,
    load_active_config,
    reset_config_cache,
    validate_config_payload,
)
from apps.api.core.conversation.memory import forget_facts, load_memory, save_facts
from apps.api.core.conversation.outcome import record_disposition
from apps.api.core.conversation.types import MEMORY_KEYS, Decision, Understanding
from apps.api.db.models import Lead, Org, User
from apps.api.db.models.conversation_engine import ConversationMemory
from apps.api.routers.conversation_engine_config import ConfigSettingsIn, _version_out

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER_A = uuid.UUID("00000000-0000-0000-0000-00000000aaaa")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000bbbb")


@pytest.fixture(autouse=True)
def _clean() -> None:
    reset_config_cache()
    runtime_stats.reset()
    yield
    reset_config_cache()
    runtime_stats.reset()


def _payload(**settings_: Any) -> dict[str, Any]:
    return {"scenarios": [s.to_json() for s in DEFAULT_SCENARIOS], "settings": {"low_confidence_action": "script", **settings_}}


# --- the opt-in setting ----------------------------------------------------------------------------------------------------


def test_the_mode_is_off_unless_an_org_publishes_it() -> None:
    assert default_config().enhanced_dialogue is False
    assert config_from_json(None).enhanced_dialogue is False
    assert config_from_json(_payload()).enhanced_dialogue is False
    assert config_from_json(_payload(enhanced_dialogue=True)).enhanced_dialogue is True
    assert config_from_json(_payload(enhanced_dialogue="true")).enhanced_dialogue is False  # only a real boolean counts


def test_the_setting_must_be_a_boolean() -> None:
    assert validate_config_payload(_payload(enhanced_dialogue=True)) == []
    assert validate_config_payload(_payload(enhanced_dialogue=False)) == []
    assert any("enhanced_dialogue" in p for p in validate_config_payload(_payload(enhanced_dialogue="yes")))


def test_the_stored_and_returned_shape_is_unchanged_for_orgs_that_do_not_opt_in() -> None:
    off = full_config_json(config_from_json(_payload()))
    assert off["settings"] == {"low_confidence_action": "script"}  # the key only appears when switched on
    on = full_config_json(config_from_json(_payload(enhanced_dialogue=True)))
    assert on["settings"] == {"low_confidence_action": "script", "enhanced_dialogue": True}
    assert ConfigSettingsIn().enhanced_dialogue is False


async def test_a_published_opt_in_is_what_the_engine_loads(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_A, name="A"))
    db_session.add(Org(id=ORG_B, name="B"))
    await db_session.commit()
    await builder.save_draft(db_session, ORG_A, "t", _payload(enhanced_dialogue=True), None)
    row = await builder.publish_draft(db_session, ORG_A, "t", None)
    assert (await load_active_config(db_session, ORG_A)).enhanced_dialogue is True
    assert (await load_active_config(db_session, ORG_B)).enhanced_dialogue is False  # another org is unaffected
    assert _version_out(row, with_scenarios=False).settings == {
        "low_confidence_action": "script", "enhanced_dialogue": True
    }


async def test_the_api_output_for_an_org_that_did_not_opt_in_is_exactly_as_before(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_A, name="A"))
    await db_session.commit()
    await builder.save_draft(db_session, ORG_A, "t", _payload(), None)
    row = await builder.publish_draft(db_session, ORG_A, "t", None)
    assert _version_out(row, with_scenarios=False).settings == {"low_confidence_action": "script"}


async def test_a_non_boolean_setting_is_rejected_when_saving(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_A, name="A"))
    await db_session.commit()
    with pytest.raises(BuilderError):
        await builder.save_draft(db_session, ORG_A, "t", _payload(enhanced_dialogue="maybe"), None)


# --- memory: whitelisting and isolation -----------------------------------------------------------------------------------------


async def _two_orgs(db: AsyncSession) -> None:
    db.add_all([Org(id=ORG_A, name="A"), Org(id=ORG_B, name="B")])
    db.add_all([User(id=USER_A, org_id=ORG_A, phone="+910000000001"), User(id=USER_B, org_id=ORG_B, phone="+910000000001")])
    await db.commit()


async def test_enhanced_keys_are_ignored_unless_the_caller_allows_them(db_session: AsyncSession) -> None:
    await _two_orgs(db_session)
    facts = {"name": "Asha", "property_info": "500 sq ft", "appointment_asks": "1"}
    written = await save_facts(db_session, ORG_A, USER_A, facts)  # the default whitelist
    assert written == ["name"]
    wider = (*MEMORY_KEYS, *dialogue.ENHANCED_MEMORY_KEYS)
    written = await save_facts(db_session, ORG_A, USER_A, facts, allowed_keys=wider)
    assert set(written) == {"name", "property_info", "appointment_asks"}
    assert (await load_memory(db_session, ORG_A, USER_A))["property_info"] == "500 sq ft"


async def test_a_key_outside_every_whitelist_is_never_stored(db_session: AsyncSession) -> None:
    await _two_orgs(db_session)
    written = await save_facts(db_session, ORG_A, USER_A, {"password": "x"}, allowed_keys=("password",))
    assert written == []  # even if a caller allowed it, a key with no defined scope is not storable


async def test_the_base_engine_does_not_store_enhanced_facts(db_session: AsyncSession) -> None:
    await _two_orgs(db_session)
    und = Understanding("discovery", "x", "neutral", "en", 0.9, "rules", facts={"property_info": "500 sq ft", "name": "Asha"})
    await engine._save_facts(db_session, ORG_A, USER_A, und, enhanced=False)
    assert await load_memory(db_session, ORG_A, USER_A) == {"name": "Asha"}
    await engine._save_facts(db_session, ORG_A, USER_A, und, extra={"appointment_asks": "1"}, enhanced=True)
    assert (await load_memory(db_session, ORG_A, USER_A))["appointment_asks"] == "1"


async def test_forgetting_a_fact_touches_only_that_org_and_customer(db_session: AsyncSession) -> None:
    await _two_orgs(db_session)
    wider = (*MEMORY_KEYS, *dialogue.ENHANCED_MEMORY_KEYS)
    for org, user in ((ORG_A, USER_A), (ORG_B, USER_B)):
        await save_facts(db_session, org, user, {"property_info_pending": "800 sq ft", "name": "X"}, allowed_keys=wider)
    await forget_facts(db_session, ORG_A, USER_A, ["property_info_pending"])
    await db_session.commit()
    assert "property_info_pending" not in await load_memory(db_session, ORG_A, USER_A)
    assert (await load_memory(db_session, ORG_B, USER_B))["property_info_pending"] == "800 sq ft"
    rows = (await db_session.execute(select(ConversationMemory).where(ConversationMemory.org_id == ORG_B))).scalars().all()
    assert {r.key for r in rows} == {"property_info_pending", "name"}


async def test_the_same_customer_number_in_two_orgs_keeps_separate_memory(db_session: AsyncSession) -> None:
    await _two_orgs(db_session)
    wider = (*MEMORY_KEYS, *dialogue.ENHANCED_MEMORY_KEYS)
    await save_facts(db_session, ORG_A, USER_A, {"meeting_preference": "online"}, allowed_keys=wider)
    await db_session.commit()
    assert await load_memory(db_session, ORG_B, USER_B) == {}


# --- decision statistics --------------------------------------------------------------------------------------------------------------


def test_stats_report_counts_percentiles_and_fallback_rate() -> None:
    assert runtime_stats.snapshot() == {}
    for ms in (100, 200, 300, 400):
        runtime_stats.record("voice", "ok", ms)
    runtime_stats.record("voice", "timeout", 900)
    runtime_stats.record("voice", "smart_script")  # a deliberate hand-off, not a failure
    snap = runtime_stats.snapshot()["voice"]
    assert snap["decisions"] == 6 and snap["outcomes"] == {"ok": 4, "timeout": 1, "smart_script": 1}
    assert snap["fallback_rate"] == round(1 / 6, 4)
    assert snap["latency_ms"] == {"n": 5, "p50": 300, "p95": 900, "p99": 900}


def test_stats_are_bounded_and_recording_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(1500):
        runtime_stats.record("voice", "ok", i)
    assert runtime_stats.snapshot()["voice"]["latency_ms"]["n"] == 1000  # the window, not the whole history
    runtime_stats.record("voice", "ok", "not a number")  # type: ignore[arg-type]
    runtime_stats.record(None, None, None)  # type: ignore[arg-type]


async def test_the_diagnostic_endpoint_shows_decision_stats_behind_the_admin_token(client: AsyncClient) -> None:
    runtime_stats.record("voice", "ok", 120)
    assert (await client.get("/diag/voice-latency")).status_code == 403
    response = await client.get("/diag/voice-latency", headers={"X-Admin-Token": settings.admin_token})
    assert response.status_code == 200
    assert response.json()["decisions"]["voice"]["decisions"] == 1


# --- the CRM disposition write ----------------------------------------------------------------------------------------------------------


async def _lead(db: AsyncSession, org: uuid.UUID, user: uuid.UUID, tags: list[str] | None = None) -> Lead:
    lead = Lead(org_id=org, user_id=user, phone="+910000000001", tags=tags)
    db.add(lead)
    await db.commit()
    return lead


def _und(**kw: Any) -> Understanding:
    return Understanding(kw.pop("scenario", "interested"), "x", "neutral", "en", 0.9, "rules", **kw)


async def test_the_disposition_lands_in_the_leads_tags_and_keeps_other_tags(db_session: AsyncSession) -> None:
    await _two_orgs(db_session)
    lead = await _lead(db_session, ORG_A, USER_A, ["vip"])
    stored = await record_disposition(
        db_session, org_id=ORG_A, user_id=USER_A,
        understanding=_und(signals=frozenset({dialogue.HESITATION})),
        decision=Decision("none", {}, "hesitation_support"), action_result=None,
    )
    assert stored == "undecided"
    await db_session.refresh(lead)
    assert lead.tags == ["vip", "disposition:undecided"] and lead.status == "new"  # status is left to the existing tools


async def test_a_confirmed_booking_is_not_downgraded_by_a_later_hesitation(db_session: AsyncSession) -> None:
    await _two_orgs(db_session)
    lead = await _lead(db_session, ORG_A, USER_A)
    await record_disposition(
        db_session, org_id=ORG_A, user_id=USER_A, understanding=_und(scenario="appointment_request"),
        decision=Decision("book_appointment", {}, "appointment_confirmed"), action_result={"status": "ok"},
    )
    changed = await record_disposition(
        db_session, org_id=ORG_A, user_id=USER_A, understanding=_und(signals=frozenset({dialogue.HESITATION})),
        decision=Decision("none", {}, "hesitation_support"), action_result=None,
    )
    await db_session.refresh(lead)
    assert changed is None and lead.tags == ["disposition:meeting_confirmed"]


async def test_the_disposition_is_org_scoped_and_never_creates_a_lead(db_session: AsyncSession) -> None:
    await _two_orgs(db_session)
    other = await _lead(db_session, ORG_B, USER_B)  # same phone, different org
    result = await record_disposition(
        db_session, org_id=ORG_A, user_id=USER_A, understanding=_und(),
        decision=Decision("none", {}, "interested"), action_result=None,
    )
    assert result is None  # org A has no lead for this user: nothing is created or borrowed
    await db_session.refresh(other)
    assert other.tags is None
    count = len((await db_session.execute(select(Lead))).scalars().all())
    assert count == 1


async def test_a_failure_while_tagging_never_breaks_the_turn(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    await _two_orgs(db_session)
    await _lead(db_session, ORG_A, USER_A)

    async def boom() -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(db_session, "commit", boom)
    result = await record_disposition(
        db_session, org_id=ORG_A, user_id=USER_A, understanding=_und(),
        decision=Decision("none", {}, "interested"), action_result=None,
    )
    assert result is None  # swallowed, logged, rolled back
