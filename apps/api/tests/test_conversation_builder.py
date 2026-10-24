"""Scenario Builder, simulator, versioning, audit, Smart Script, metrics,
silence handling and the scenario-library additions (quotation 2.1 - 2.6)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import date
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.conversation import builder
from apps.api.core.conversation.config import (
    DEFAULT_SCENARIOS,
    config_from_json,
    default_config,
    validate_config_payload,
)
from apps.api.core.conversation.decide import apply_scenario_actions, decide
from apps.api.core.conversation.engine import run_engine
from apps.api.core.conversation.memory import load_memory
from apps.api.core.conversation.silence import SilenceMonitor
from apps.api.core.conversation.types import Decision, Understanding
from apps.api.core.conversation.understand import classify_with_rules
from apps.api.core.llm import ChatResult
from apps.api.db.models import Appointment, Conversation, Org, User
from apps.api.db.models.conversation_engine import (
    ConversationConfigVersion,
    ConversationTurn,
)
from apps.api.deps import get_db, get_redis_dep
from apps.api.tests.conftest import FakeRedis

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000ccc")
TODAY = date(2026, 10, 15)
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}
ACTOR = "admin@example.com"


@pytest.fixture(autouse=True)
def _pin_default_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "default_org_id", str(ORG_ID))


async def _seed(db: AsyncSession, *, mode: str = "human_conversation") -> Conversation:
    db.add(Org(id=ORG_ID, name="Test Org", conversation_mode=mode))
    db.add(Org(id=OTHER_ORG_ID, name="Other Org"))
    db.add(User(id=USER_ID, org_id=ORG_ID, phone="+910000000001", name="Test"))
    conversation = Conversation(org_id=ORG_ID, user_id=USER_ID, channel="whatsapp")
    db.add(conversation)
    await db.commit()
    return conversation


async def _no_llm(messages: list[dict[str, Any]]) -> ChatResult:
    raise AssertionError("LLM must not be called here")


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, action_id: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action_id, args))
        return {"status": "ok"}


async def _run(db: AsyncSession, conversation: Conversation, text: str, **kwargs: Any):
    kwargs.setdefault("llm", _no_llm)
    kwargs.setdefault("dispatch", _Recorder())
    return await run_engine(
        db,
        org_id=ORG_ID,
        user_id=USER_ID,
        conversation_id=conversation.id,
        channel="whatsapp",
        text=text,
        history=[],
        api_key=None,
        **kwargs,
    )


def _scenario(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "warranty_query",
        "name": "Warranty query",
        "situation": "Customer asks about warranty",
        "examples": ["what about warranty", "warranty kitni milti hai"],
        "goal": "Explain warranty using approved facts",
        "should_do": "Answer briefly",
        "must_avoid": "Promising extended warranty",
        "actions": ["none"],
        "next_scenarios": ["interested"],
        "enabled": True,
        "reply_example": "Our warranty details are being checked for you.",
    }
    base.update(overrides)
    return base


def _payload(*extra: dict[str, Any], base: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    scenarios = base if base is not None else [s.to_json() for s in DEFAULT_SCENARIOS]
    return {"scenarios": [*scenarios, *extra]}


# --- scenario library ----------------------------------------------------------


def test_library_covers_every_scenario_in_the_quotation() -> None:
    ids = {s.id for s in DEFAULT_SCENARIOS}
    # greeting, identification, discovery, interested, price question, price
    # objection, discount, busy, callback, WhatsApp request, not interested,
    # angry, confused, unknown question, appointment, human handoff, closing
    assert ids >= {
        "greeting", "identification", "discovery", "interested", "price_question", "objection",
        "discount_request", "busy_callback", "callback_request", "whatsapp_info_request",
        "not_interested", "angry", "confused", "unknown_question", "appointment_request",
        "human_handoff", "closing",
    }
    assert len(DEFAULT_SCENARIOS) == 17


def test_default_config_passes_its_own_regression_gate() -> None:
    assert builder.regression_problems(default_config()) == []


@pytest.mark.parametrize(
    ("text", "scenario"),
    [
        ("yes speaking", "identification"),
        ("haan ji bol raha hoon", "identification"),
        ("we run a travel agency", "discovery"),
        ("mera clothing business hai", "discovery"),
        ("any offer available?", "discount_request"),
        ("koi offer hai kya", "discount_request"),
        ("discount milega?", "discount_request"),
        ("thank you, bye", "closing"),
        ("ok thanks", "closing"),
        ("dhanyavaad", "closing"),
        ("I am busy, call me later", "busy_callback"),
        ("please call me at 6 pm", "callback_request"),
        ("call me tomorrow", "callback_request"),
    ],
)
def test_new_scenarios_are_recognised(text: str, scenario: str) -> None:
    result = classify_with_rules(text, state=None, today=TODAY, config=default_config())
    assert result is not None and result.scenario == scenario


def test_closing_needs_a_short_message() -> None:
    result = classify_with_rules(
        "thank you for the details but please send me the price list on whatsapp",
        state=None,
        today=TODAY,
        config=default_config(),
    )
    assert result is None or result.scenario != "closing"


def test_business_type_and_pain_point_are_extracted() -> None:
    result = classify_with_rules(
        "we run a travel agency and our problem is manual lead follow-up",
        state=None,
        today=TODAY,
        config=default_config(),
    )
    assert result is not None and result.scenario == "discovery"
    assert result.facts["business_type"] == "Travel Agency"
    assert result.facts["pain_point"].startswith("manual lead follow-up")


def test_no_problem_is_not_a_pain_point() -> None:
    result = classify_with_rules("no problem, I am busy now", state=None, today=TODAY)
    assert result is not None and "pain_point" not in result.facts


async def test_pain_point_is_remembered_across_conversations(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    result = await _run(
        db_session, conversation, "we run a travel agency, our problem is manual follow-up"
    )
    assert result.reply is not None
    memory = await load_memory(db_session, ORG_ID, USER_ID)
    assert memory["business_type"] == "Travel Agency"
    assert "manual follow-up" in memory["pain_point"]


async def test_discovery_asks_for_what_is_missing(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    first = await _run(db_session, conversation, "our problem is manual follow-up")
    assert first.reply is not None and "business" in first.reply.lower()
    second = await _run(db_session, conversation, "we run a travel agency")
    # Business and pain point are both known now: move to a next step, don't ask again.
    assert second.reply is not None and "call" in second.reply.lower()


# --- config validation, disabling, custom scenarios -----------------------------


def test_validation_rejects_bad_configs() -> None:
    assert validate_config_payload({"nope": 1})
    assert validate_config_payload(_payload(_scenario(id="Bad Id")))
    assert validate_config_payload(_payload(_scenario(actions=["drop_database"])))
    assert validate_config_payload(_payload(_scenario(examples=[])))
    assert validate_config_payload(_payload(_scenario(goal="")))
    assert validate_config_payload(_payload(_scenario(next_scenarios=["ghost"])))
    assert validate_config_payload(_payload(_scenario(), _scenario()))  # duplicate id
    assert validate_config_payload(_payload(_scenario())) == []


def test_disabled_scenario_is_not_matched_by_rules() -> None:
    scenarios = [s.to_json() for s in DEFAULT_SCENARIOS]
    for item in scenarios:
        if item["id"] == "price_question":
            item["enabled"] = False
    config = config_from_json({"scenarios": scenarios})
    result = classify_with_rules("what is the price", state=None, today=TODAY, config=config)
    assert result is None or result.scenario != "price_question"


def test_custom_scenario_example_beats_generic_rules() -> None:
    config = config_from_json(_payload(_scenario()))
    result = classify_with_rules("what about warranty", state=None, today=TODAY, config=config)
    assert result is not None and result.scenario == "warranty_query"


async def test_custom_scenario_gives_a_guided_reply_without_inventing(
    db_session: AsyncSession,
) -> None:
    conversation = await _seed(db_session)
    config = config_from_json(_payload(_scenario()))
    recorder = _Recorder()
    result = await _run(
        db_session, conversation, "what about warranty", config=config, dispatch=recorder
    )
    assert result.reply == "Our warranty details are being checked for you."  # admin's example, no LLM
    assert recorder.calls == []


async def test_custom_scenario_can_run_its_one_approved_action(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    config = config_from_json(
        _payload(_scenario(id="vip_request", name="VIP", examples=["i am a vip"], actions=["transfer_to_human"]))
    )
    recorder = _Recorder()
    result = await _run(db_session, conversation, "I am a VIP", config=config, dispatch=recorder)
    assert result.reply is not None
    assert [name for name, _ in recorder.calls] == ["transfer_to_human"]


def test_scenario_can_only_narrow_actions() -> None:
    config = config_from_json(
        _payload(base=[{**s.to_json(), "actions": ["none"]} if s.id == "busy_callback" else s.to_json()
                       for s in DEFAULT_SCENARIOS])
    )
    understanding = Understanding(
        scenario="busy_callback", intent="x", emotion="neutral", language="en",
        confidence=0.9, source="rules", facts={"callback_date": "2026-10-16", "callback_time_hhmm": "17:00"},
    )
    decision = decide(understanding, text="busy", memory={}, knowledge=[], config=config)
    assert decision.action_id == "none"
    assert decision.reply_kind == "guided"  # must not claim a callback was booked


def test_apply_scenario_actions_keeps_allowed_actions() -> None:
    decision = Decision("transfer_to_human", {"reason": "x", "urgency": "low"}, "handoff")
    scenario = default_config().get("human_handoff")
    assert apply_scenario_actions(decision, scenario) is decision


# --- Smart Script -------------------------------------------------------------------


async def test_smart_script_leaves_wording_to_the_script_but_adds_guidance(
    db_session: AsyncSession,
) -> None:
    conversation = await _seed(db_session, mode="smart_script")
    result = await _run(db_session, conversation, "what is the price", smart_script=True)
    assert result.reply is None
    assert result.fallback_reason == "smart_script"
    assert result.guidance and "approved pricing" in result.guidance
    turn = (await db_session.execute(select(ConversationTurn))).scalars().one()
    assert turn.fallback_reason == "smart_script" and turn.scenario == "price_question"


async def test_smart_script_still_decides_action_scenarios(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session, mode="smart_script")
    recorder = _Recorder()
    result = await _run(
        db_session, conversation, "I am not interested", smart_script=True, dispatch=recorder
    )
    assert result.reply is not None
    assert [name for name, _ in recorder.calls] == ["mark_not_interested"]


class _FakeAgentRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def rpush(self, key: str, value: str) -> int:
        return 1


async def test_smart_script_guidance_reaches_the_script_prompt(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.api.core import agent as agent_module
    from apps.api.core.agent import agent_core

    await _seed(db_session, mode="smart_script")
    monkeypatch.setattr(agent_module, "get_redis_pool", lambda: _FakeAgentRedis())
    seen: list[list[dict[str, Any]]] = []

    async def fake_chat(messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        seen.append(messages)
        return ChatResult(content="Script answer", tokens_in=1, tokens_out=1)

    monkeypatch.setattr(agent_module, "chat_completion", fake_chat)
    reply = await agent_core.handle_turn(
        db=db_session, user_id=USER_ID, channel="whatsapp", input_text="what is the price"
    )
    assert reply == "Script answer"
    system = seen[-1][0]["content"]
    assert "CONVERSATION GUIDANCE FOR THIS TURN" in system and "approved pricing" in system


# --- versioning: draft, publish, rollback, audit, pinning ------------------------------


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    from apps.api.main import create_app

    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_get_redis() -> AsyncGenerator[FakeRedis, None]:
        yield FakeRedis()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_dep] = override_get_redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_builder_endpoints_require_auth(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    for path in ("/conversation-engine/config", "/conversation-engine/audit", "/conversation-engine/metrics"):
        assert (await client.get(path)).status_code in (401, 403)


async def test_draft_publish_rollback_flow(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    overview = (await client.get("/conversation-engine/config", headers=ADMIN_HEADERS)).json()
    assert overview["active"] is None and overview["draft"] is None
    assert len(overview["base"]) == 17

    # Draft -> publish v1 with a custom scenario.
    saved = await client.put(
        "/conversation-engine/config/draft",
        headers=ADMIN_HEADERS,
        json={"scenarios": _payload(_scenario())["scenarios"], "note": "add warranty"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "draft" and saved.json()["version"] is None
    v1 = await client.post(
        "/conversation-engine/config/draft/publish", headers=ADMIN_HEADERS, json={"note": "go"}
    )
    assert v1.status_code == 200, v1.text
    assert v1.json()["version"] == 1 and v1.json()["status"] == "published"

    # Second version disables closing.
    scenarios = [s for s in _payload(_scenario())["scenarios"]]
    for item in scenarios:
        if item["id"] == "closing":
            item["enabled"] = False
    await client.put(
        "/conversation-engine/config/draft", headers=ADMIN_HEADERS, json={"scenarios": scenarios}
    )
    v2 = (
        await client.post("/conversation-engine/config/draft/publish", headers=ADMIN_HEADERS, json={})
    ).json()
    assert v2["version"] == 2

    # Rolling back to v1 publishes a COPY as v3; v1 and v2 rows are untouched.
    rolled = await client.post(
        "/conversation-engine/config/rollback", headers=ADMIN_HEADERS, json={"version": 1}
    )
    assert rolled.status_code == 200
    assert rolled.json()["version"] == 3 and rolled.json()["rolled_back_from"] == 1
    versions = (await client.get("/conversation-engine/config/versions", headers=ADMIN_HEADERS)).json()
    assert [v["version"] for v in versions] == [3, 2, 1]
    active = (await client.get("/conversation-engine/config", headers=ADMIN_HEADERS)).json()["active"]
    assert active["version"] == 3
    closing = next(s for s in active["scenarios"] if s["id"] == "closing")
    assert closing["enabled"] is True  # v1's setting, not v2's
    v2_row = (await client.get("/conversation-engine/config/versions/2", headers=ADMIN_HEADERS)).json()
    assert next(s for s in v2_row["scenarios"] if s["id"] == "closing")["enabled"] is False

    # Already-active version cannot be "rolled back" to.
    again = await client.post(
        "/conversation-engine/config/rollback", headers=ADMIN_HEADERS, json={"version": 3}
    )
    assert again.status_code == 409

    # Audit trail: every change, newest first.
    audit = (await client.get("/conversation-engine/audit", headers=ADMIN_HEADERS)).json()
    actions = [event["action"] for event in audit]
    assert actions.count("config.published") == 2
    assert "config.rolled_back" in actions and "config.draft_created" in actions
    assert all(event["actor"] for event in audit)

    status = (await client.get("/conversation-engine/status", headers=ADMIN_HEADERS)).json()
    assert status["active_config_version"] == 3 and status["has_draft"] is False


async def test_published_versions_are_immutable(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    await client.put(
        "/conversation-engine/config/draft", headers=ADMIN_HEADERS, json=_payload(_scenario())
    )
    await client.post("/conversation-engine/config/draft/publish", headers=ADMIN_HEADERS, json={})
    # Editing now creates a NEW draft; version 1 keeps its content.
    edited = _payload(_scenario(name="Renamed"))
    await client.put("/conversation-engine/config/draft", headers=ADMIN_HEADERS, json=edited)
    v1 = (await client.get("/conversation-engine/config/versions/1", headers=ADMIN_HEADERS)).json()
    assert next(s for s in v1["scenarios"] if s["id"] == "warranty_query")["name"] == "Warranty query"
    rows = (await db_session.execute(select(ConversationConfigVersion))).scalars().all()
    assert sorted((r.status, r.version) for r in rows if r.version) == [("published", 1)]
    assert sum(1 for r in rows if r.status == "draft") == 1


async def test_invalid_draft_is_rejected_with_reasons(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    response = await client.put(
        "/conversation-engine/config/draft",
        headers=ADMIN_HEADERS,
        json=_payload(_scenario(actions=["drop_database"])),
    )
    assert response.status_code == 422
    assert "not an approved action" in response.json()["detail"]


async def test_publish_is_blocked_when_it_breaks_a_validated_path(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    hijack = _scenario(id="hijack", name="Hijack", examples=["what is the price"])
    await client.put("/conversation-engine/config/draft", headers=ADMIN_HEADERS, json=_payload(hijack))
    response = await client.post(
        "/conversation-engine/config/draft/publish", headers=ADMIN_HEADERS, json={}
    )
    assert response.status_code == 422
    assert "what is the price" in response.json()["detail"]
    # Nothing was published.
    versions = (await client.get("/conversation-engine/config/versions", headers=ADMIN_HEADERS)).json()
    assert versions == []


async def test_publish_without_draft_and_discard(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    assert (
        await client.post("/conversation-engine/config/draft/publish", headers=ADMIN_HEADERS, json={})
    ).status_code == 404
    await client.put("/conversation-engine/config/draft", headers=ADMIN_HEADERS, json=_payload(_scenario()))
    assert (await client.delete("/conversation-engine/config/draft", headers=ADMIN_HEADERS)).status_code == 204
    assert (await client.delete("/conversation-engine/config/draft", headers=ADMIN_HEADERS)).status_code == 404


async def test_builder_is_org_scoped(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    other_actor = "someone"
    other = await builder.save_draft(db_session, OTHER_ORG_ID, other_actor, _payload(_scenario()), None)
    await builder.publish_draft(db_session, OTHER_ORG_ID, other_actor, None)
    del other
    assert (await client.get("/conversation-engine/config/versions", headers=ADMIN_HEADERS)).json() == []
    assert (
        await client.get("/conversation-engine/config/versions/1", headers=ADMIN_HEADERS)
    ).status_code == 404
    assert (
        await client.post("/conversation-engine/config/rollback", headers=ADMIN_HEADERS, json={"version": 1})
    ).status_code == 404
    audit = (await client.get("/conversation-engine/audit", headers=ADMIN_HEADERS)).json()
    assert audit == []


async def test_knowledge_changes_are_audited(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    created = await client.post(
        "/conversation-engine/knowledge",
        headers=ADMIN_HEADERS,
        json={"kind": "pricing", "title": "Starter", "body": "Rs 999"},
    )
    item_id = created.json()["id"]
    await client.patch(f"/conversation-engine/knowledge/{item_id}", headers=ADMIN_HEADERS, json={"active": False})
    await client.delete(f"/conversation-engine/knowledge/{item_id}", headers=ADMIN_HEADERS)
    audit = (await client.get("/conversation-engine/audit", headers=ADMIN_HEADERS)).json()
    assert sorted(e["action"] for e in audit) == ["knowledge.created", "knowledge.deleted", "knowledge.updated"]


async def test_conversation_keeps_the_version_it_started_with(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    scenarios = [s.to_json() for s in DEFAULT_SCENARIOS]
    await builder.save_draft(db_session, ORG_ID, ACTOR, {"scenarios": scenarios}, None)
    v1 = await builder.publish_draft(db_session, ORG_ID, ACTOR, None)

    first = await _run(db_session, conversation, "hello")
    assert first.reply is not None

    # v2 switches the greeting off while this conversation is under way.
    for item in scenarios:
        if item["id"] == "greeting":
            item["enabled"] = False
    await builder.save_draft(db_session, ORG_ID, ACTOR, {"scenarios": scenarios}, None)
    v2 = await builder.publish_draft(db_session, ORG_ID, ACTOR, None)
    assert v2.version == 2

    same_call = await _run(db_session, conversation, "hello")
    assert same_call.reply is not None  # still v1: greeting is on

    fresh = Conversation(org_id=ORG_ID, user_id=USER_ID, channel="whatsapp")
    db_session.add(fresh)
    await db_session.commit()
    new_call = await _run(db_session, fresh, "hello")
    assert new_call.reply is None  # v2: greeting is off, so the script takes over

    versions = {
        (t.conversation_id, t.config_version)
        for t in (await db_session.execute(select(ConversationTurn))).scalars()
        if t.fallback_reason is None
    }
    assert versions == {(conversation.id, v1.version)}


# --- simulator ----------------------------------------------------------------------------


async def test_simulator_runs_the_pipeline_and_changes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    before = await db_session.scalar(select(func.count()).select_from(ConversationTurn))
    response = await client.post(
        "/conversation-engine/simulate",
        headers=ADMIN_HEADERS,
        json={"text": "I am busy, call me tomorrow at 5 pm", "use_llm": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scenario"] == "busy_callback" and body["action_id"] == "request_callback"
    assert body["would_run_action"] is True and body["state_after"] == "callback_set"
    assert body["memory_after"]["callback_time"].endswith("17:00")
    assert body["reply"] and body["config_source"] == "active"
    # Nothing executed, nothing logged.
    assert await db_session.scalar(select(func.count()).select_from(ConversationTurn)) == before


async def test_simulator_uses_the_draft_and_carries_state(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    await client.put("/conversation-engine/config/draft", headers=ADMIN_HEADERS, json=_payload(_scenario()))
    first = (
        await client.post(
            "/conversation-engine/simulate",
            headers=ADMIN_HEADERS,
            json={"text": "what about warranty", "source": "draft", "use_llm": False},
        )
    ).json()
    assert first["scenario"] == "warranty_query" and first["config_source"] == "draft"
    assert first["reply"] == "Our warranty details are being checked for you."
    # The active version has no such scenario.
    active = (
        await client.post(
            "/conversation-engine/simulate",
            headers=ADMIN_HEADERS,
            json={"text": "what about warranty", "source": "active", "use_llm": False},
        )
    ).json()
    assert active["scenario"] != "warranty_query"

    ask = (
        await client.post(
            "/conversation-engine/simulate",
            headers=ADMIN_HEADERS,
            json={"text": "I am busy, call me later", "use_llm": False},
        )
    ).json()
    assert ask["state_after"] == "awaiting_callback_time"
    answer = (
        await client.post(
            "/conversation-engine/simulate",
            headers=ADMIN_HEADERS,
            json={"text": "tomorrow 4 pm", "state": ask["state_after"], "memory": ask["memory_after"], "use_llm": False},
        )
    ).json()
    assert answer["scenario"] == "busy_callback" and answer["action_id"] == "request_callback"


async def test_simulator_reports_smart_script_handoff(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session, mode="smart_script")
    body = (
        await client.post(
            "/conversation-engine/simulate",
            headers=ADMIN_HEADERS,
            json={"text": "what is the price", "use_llm": False},
        )
    ).json()
    assert body["fallback_reason"] == "smart_script" and body["guidance"]


# --- metrics + review -------------------------------------------------------------------------


async def test_metrics_and_turn_review(client: AsyncClient, db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)

    def turn(**kw: Any) -> ConversationTurn:
        base: dict[str, Any] = {
            "org_id": ORG_ID, "user_id": USER_ID, "conversation_id": conversation.id,
            "channel": "whatsapp", "mode": "live", "user_text": "x", "latency_ms": 100,
        }
        base.update(kw)
        return ConversationTurn(**base)

    other_conversation = Conversation(org_id=ORG_ID, user_id=USER_ID, channel="voice")
    db_session.add(other_conversation)
    await db_session.commit()
    db_session.add_all(
        [
            turn(scenario="busy_callback", state="callback_set", action_id="request_callback",
                 action_result={"status": "ok"}, latency_ms=50),
            turn(scenario="appointment_request", state="appointment_set", action_id="book_appointment",
                 action_result={"status": "ok"}, latency_ms=150),
            turn(scenario="price_question", fallback_reason="timeout", latency_ms=300),
            turn(scenario="price_question", fallback_reason="smart_script"),
            turn(conversation_id=other_conversation.id, channel="voice", scenario="human_handoff",
                 state="handed_off", action_id="transfer_to_human", action_result={"status": "ok"}),
        ]
    )
    await db_session.commit()
    first = (await db_session.execute(select(ConversationTurn).order_by(ConversationTurn.created_at))).scalars().first()

    reviewed = await client.patch(
        f"/conversation-engine/turns/{first.id}/review", headers=ADMIN_HEADERS, json={"label": "correct"}
    )
    assert reviewed.status_code == 200 and reviewed.json()["review_label"] == "correct"
    wrong = (
        await db_session.execute(select(ConversationTurn).where(ConversationTurn.scenario == "appointment_request"))
    ).scalars().one()
    await client.patch(
        f"/conversation-engine/turns/{wrong.id}/review",
        headers=ADMIN_HEADERS,
        json={"label": "incorrect", "correct_scenario": "interested"},
    )

    metrics = (await client.get("/conversation-engine/metrics?days=7", headers=ADMIN_HEADERS)).json()
    assert metrics["turns"] == 5
    assert metrics["fallbacks"] == 1 and metrics["fallback_rate"] == 0.2  # smart_script is by design
    assert metrics["handed_to_script_by_design"] == 1
    assert metrics["fallback_reasons"] == {"timeout": 1}
    assert metrics["scenario_accuracy"] == 0.5 and metrics["reviewed_turns"] == 2
    assert metrics["conversations"] == 2
    assert metrics["appointments_booked"] == 1 and metrics["appointment_conversion"] == 0.5
    assert metrics["transfers"] == 1 and metrics["transfer_rate"] == 0.5
    assert metrics["latency_ms"]["p95"] is not None
    only_voice = (await client.get("/conversation-engine/metrics?channel=voice", headers=ADMIN_HEADERS)).json()
    assert only_voice["turns"] == 1

    # A turn from another org cannot be reviewed.
    assert (
        await client.patch(
            f"/conversation-engine/turns/{uuid.uuid4()}/review", headers=ADMIN_HEADERS, json={"label": "correct"}
        )
    ).status_code == 404


async def test_tuning_report(client: AsyncClient, db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)

    def turn(**kw: Any) -> ConversationTurn:
        base: dict[str, Any] = {
            "org_id": ORG_ID, "user_id": USER_ID, "conversation_id": conversation.id,
            "channel": "whatsapp", "mode": "live", "user_text": "x", "latency_ms": 100, "source": "rules",
        }
        base.update(kw)
        return ConversationTurn(**base)

    db_session.add_all(
        [
            turn(scenario="busy_callback", confidence=0.9),
            turn(scenario="price_question", source="llm", confidence=0.5, latency_ms=800,
                 fallback_reason="low_confidence", user_text="kitne ka padega"),
            turn(scenario="unknown_question", source="llm", confidence=0.8, user_text="do you ship to Nepal"),
            turn(scenario="price_question", review_label="incorrect", review_scenario="discount_request"),
        ]
    )
    await db_session.commit()

    report = (await client.get("/conversation-engine/metrics/tuning", headers=ADMIN_HEADERS)).json()
    assert report["turns"] == 4 and report["llm_turns"] == 2 and report["llm_share"] == 0.5
    assert report["by_source"]["llm"]["p95_ms"] == 800
    assert report["weakest_scenarios"][0]["scenario"] == "price_question"
    assert report["scenario_corrections"] == [
        {"predicted": "price_question", "should_be": "discount_request", "count": 1}
    ]
    assert report["not_understood_samples"] == ["kitne ka padega"]
    assert report["knowledge_gap_samples"] == ["do you ship to Nepal"]
    assert report["low_confidence_near_misses"] == 1
    assert any("model call" in s for s in report["suggestions"])
    # Plain members cannot read it.
    assert (await client.get("/conversation-engine/metrics/tuning")).status_code in (401, 403)


def test_default_mode_setting_is_a_valid_mode() -> None:
    from apps.api.config import settings
    from apps.api.db.models.org import CONVERSATION_MODES

    # Phase 7 switch ships off: new orgs stay on Strict Script until it is changed.
    assert settings.conversation_default_mode == "strict_script"
    assert settings.conversation_default_mode in CONVERSATION_MODES


# --- silence handling ---------------------------------------------------------------------------


def test_silence_monitor_checks_then_flags_connection_issue() -> None:
    monitor = SilenceMonitor(check_after=8.0, max_checks=2)
    monitor.agent_finished(100.0)
    assert monitor.poll(105.0, busy=False) is None  # short silence is normal
    assert monitor.poll(108.5, busy=False) == "check"
    assert monitor.poll(120.0, busy=False) is None  # waits for the check prompt to finish
    monitor.agent_finished(110.0)
    assert monitor.poll(118.5, busy=False) == "check"
    monitor.agent_finished(120.0)
    assert monitor.poll(128.5, busy=False) == "connection_issue"
    assert monitor.issue is True
    monitor.agent_finished(130.0)
    assert monitor.poll(200.0, busy=False) is None  # declared once, never again


def test_silence_monitor_resets_when_the_caller_speaks() -> None:
    monitor = SilenceMonitor(check_after=8.0, max_checks=2)
    monitor.agent_finished(0.0)
    assert monitor.poll(9.0, busy=False) == "check"
    monitor.caller_active()
    assert monitor.checks == 0
    assert monitor.poll(50.0, busy=False) is None  # disarmed until the agent speaks again


def test_silence_monitor_never_fires_while_the_agent_is_busy() -> None:
    monitor = SilenceMonitor(check_after=8.0, max_checks=2)
    monitor.agent_finished(0.0)
    assert monitor.poll(30.0, busy=True) is None
    assert monitor.poll(30.0, busy=False) == "check"


async def test_silence_events_are_logged(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.core.conversation import voice_decision

    conversation = await _seed(db_session)

    class _Factory:
        def __call__(self) -> Any:
            return _Ctx()

    class _Ctx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr(voice_decision, "AsyncSessionLocal", _Factory())
    await voice_decision.log_silence_event(ORG_ID, USER_ID, conversation.id, kind="check")
    await voice_decision.log_silence_event(ORG_ID, USER_ID, conversation.id, kind="connection_issue")
    turns = (await db_session.execute(select(ConversationTurn).order_by(ConversationTurn.created_at))).scalars().all()
    assert [t.scenario for t in turns] == ["silence", "silence"]
    assert turns[-1].state == "connection_issue" and turns[0].state is None


# --- voice instructions for the new scenarios ------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    ["identification", "discovery_business", "discovery_pain", "discovery_offer", "discount_followup", "closing"],
)
def test_voice_instruction_exists_for_every_new_reply_kind(kind: str) -> None:
    from apps.api.core.conversation.voice_decision import VoicePlan, build_instruction

    understanding = Understanding(
        scenario="x", intent="x", emotion="neutral", language="en", confidence=0.9, source="rules"
    )
    plan = VoicePlan(understanding, Decision("none", {}, kind), {}, None, 0.0)
    text = build_instruction(plan, action_result=None)
    assert text.startswith("DECISION FOR THIS TURN") and len(text) > 80
    assert "Greet the caller warmly" not in text  # not the generic fallback branch


def test_guided_voice_instruction_carries_the_scenario_guidance() -> None:
    from apps.api.core.conversation.voice_decision import VoicePlan, build_instruction

    understanding = Understanding(
        scenario="warranty_query", intent="x", emotion="neutral", language="hinglish",
        confidence=0.9, source="rules",
    )
    plan = VoicePlan(understanding, Decision("none", {}, "guided"), {}, None, 0.0, "Goal: explain warranty.")
    assert "Goal: explain warranty." in build_instruction(plan, action_result=None)


# --- silence loop in the voice adapter ----------------------------------------------------------


class _FakeOai:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        import json

        self.sent.append(json.loads(message))


class _Log:
    def info(self, *args: Any, **kwargs: Any) -> None:
        return None


async def test_voice_silence_loop_checks_then_ends_politely(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from apps.api.channels.voice import adapter
    from apps.api.core.conversation import voice_decision

    logged: list[str] = []

    async def fake_log(org_id: Any, user_id: Any, conversation_id: Any, *, kind: str) -> None:
        logged.append(kind)

    monkeypatch.setattr(voice_decision, "log_silence_event", fake_log)
    voice_decision.set_cached_voice_mode(ORG_ID, "live")
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    monkeypatch.setattr(settings, "conversation_silence_check_seconds", 0.01)
    monkeypatch.setattr(settings, "conversation_silence_max_checks", 1)

    state = adapter.CallState(
        user_id=USER_ID, conversation_id=uuid.uuid4(), org_id=ORG_ID, base_instructions="BASE"
    )
    oai = _FakeOai()
    adapter._arm_silence(oai, state, _Log())  # the agent just finished speaking
    assert state.silence is not None and state.silence_task is not None
    await asyncio.sleep(1.3)
    assert logged == ["check"]
    check = oai.sent[-1]["response"]
    assert check["tool_choice"] == "none" and "main yahin hoon" in check["instructions"]

    state.response_active = False
    state.silence.agent_finished(0.0)  # the check prompt finished playing; still silent
    await asyncio.sleep(1.3)
    assert logged == ["check", "connection_issue"]
    assert "goodbye" in oai.sent[-1]["response"]["instructions"]
    await asyncio.sleep(0.2)
    assert state.silence_task.done()  # the loop ends once the call is flagged


async def test_voice_silence_is_off_for_orgs_not_on_live_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.channels.voice import adapter
    from apps.api.core.conversation import voice_decision

    voice_decision.set_cached_voice_mode(ORG_ID, "shadow")
    state = adapter.CallState(
        user_id=USER_ID, conversation_id=uuid.uuid4(), org_id=ORG_ID, base_instructions="BASE"
    )
    adapter._arm_silence(_FakeOai(), state, _Log())
    assert state.silence is None and state.silence_task is None


async def test_voice_plan_for_smart_script_only_adds_guidance(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.api.core.conversation import voice_decision

    await _seed(db_session, mode="smart_script")
    db_session.add(Org(id=uuid.uuid4(), name="x"))  # unrelated org, must not matter
    await db_session.commit()

    class _Factory:
        def __call__(self) -> Any:
            return _Ctx()

    class _Ctx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr(voice_decision, "AsyncSessionLocal", _Factory())
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    voice_decision.reset_flag_cache()
    org = await db_session.get(Org, ORG_ID)
    org.conversation_voice_mode = "live"
    await db_session.commit()

    conversation_id = uuid.uuid4()
    plan = await voice_decision.compute_voice_plan(ORG_ID, USER_ID, conversation_id, "what is the price")
    assert plan is not None and plan.decision.reply_kind == "script_guidance"
    assert plan.decision.action_id == "none"
    text = voice_decision.build_instruction(plan, action_result=None)
    assert "Carry on with your script" in text and "approved pricing" in text

    # An action scenario is still decided by the engine in Smart Script mode.
    busy = await voice_decision.compute_voice_plan(
        ORG_ID, USER_ID, conversation_id, "I am busy, call me tomorrow at 5 pm"
    )
    assert busy is not None and busy.decision.action_id == "request_callback"


# --- owner switches the mode; Conversation Engine is a per-org feature ---------------------------


async def test_org_admin_can_switch_mode_and_it_is_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session, mode="strict_script")
    for target in ("smart_script", "human_conversation", "strict_script"):
        response = await client.put(
            "/conversation-engine/mode", headers=ADMIN_HEADERS, json={"mode": target}
        )
        assert response.status_code == 200, response.text
        assert response.json()["conversation_mode"] == target
    org = await db_session.get(Org, ORG_ID)
    await db_session.refresh(org)
    assert org.conversation_mode == "strict_script"
    audit = (await client.get("/conversation-engine/audit", headers=ADMIN_HEADERS)).json()
    changes = [e for e in audit if e["action"] == "org.conversation_mode_changed"]
    assert len(changes) == 3 and changes[0]["detail"] == {"from": "human_conversation", "to": "strict_script"}
    # Setting the same mode again records nothing new.
    await client.put("/conversation-engine/mode", headers=ADMIN_HEADERS, json={"mode": "strict_script"})
    audit = (await client.get("/conversation-engine/audit", headers=ADMIN_HEADERS)).json()
    assert len([e for e in audit if e["action"] == "org.conversation_mode_changed"]) == 3


async def test_mode_endpoint_rejects_unknown_modes_and_needs_auth(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    assert (
        await client.put("/conversation-engine/mode", headers=ADMIN_HEADERS, json={"mode": "chaos"})
    ).status_code == 422
    assert (await client.put("/conversation-engine/mode", json={"mode": "strict_script"})).status_code in (401, 403)


async def test_mode_change_affects_only_that_org(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session, mode="strict_script")
    await client.put("/conversation-engine/mode", headers=ADMIN_HEADERS, json={"mode": "smart_script"})
    other = await db_session.get(Org, OTHER_ORG_ID)
    await db_session.refresh(other)
    assert other.conversation_mode == "strict_script"


async def test_conversation_engine_feature_can_be_switched_off_per_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from apps.api.db.models.org import AVAILABLE_ORG_FEATURES, validate_org_features

    assert "conversation_engine" in AVAILABLE_ORG_FEATURES
    assert validate_org_features(["calling", "conversation_engine"]) == ["calling", "conversation_engine"]

    await _seed(db_session)
    assert (await client.get("/conversation-engine/status", headers=ADMIN_HEADERS)).status_code == 200
    org = await db_session.get(Org, ORG_ID)
    org.enabled_features = ["calling"]  # restricted, without the engine
    await db_session.commit()
    for path in ("/conversation-engine/status", "/conversation-engine/config", "/conversation-engine/metrics"):
        blocked = await client.get(path, headers=ADMIN_HEADERS)
        assert blocked.status_code == 403, path
        assert blocked.json()["detail"]["feature"] == "conversation_engine"
    assert (
        await client.put("/conversation-engine/mode", headers=ADMIN_HEADERS, json={"mode": "smart_script"})
    ).status_code == 403
    org.enabled_features = ["calling", "conversation_engine"]
    await db_session.commit()
    assert (await client.get("/conversation-engine/status", headers=ADMIN_HEADERS)).status_code == 200


async def test_deleting_a_custom_scenario_removes_it_from_the_next_version_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    await client.put("/conversation-engine/config/draft", headers=ADMIN_HEADERS, json=_payload(_scenario()))
    assert (
        await client.post("/conversation-engine/config/draft/publish", headers=ADMIN_HEADERS, json={})
    ).json()["version"] == 1

    # The builder removes it from the draft (the Delete button) and publishes.
    without = {"scenarios": [s.to_json() for s in DEFAULT_SCENARIOS]}
    await client.put("/conversation-engine/config/draft", headers=ADMIN_HEADERS, json=without)
    assert (
        await client.post("/conversation-engine/config/draft/publish", headers=ADMIN_HEADERS, json={})
    ).json()["version"] == 2

    live = (await client.get("/conversation-engine/config", headers=ADMIN_HEADERS)).json()["active"]
    assert "warranty_query" not in [s["id"] for s in live["scenarios"]]
    old = (await client.get("/conversation-engine/config/versions/1", headers=ADMIN_HEADERS)).json()
    assert "warranty_query" in [s["id"] for s in old["scenarios"]]  # history is intact

    # The phrase no longer selects the deleted scenario.
    body = (
        await client.post(
            "/conversation-engine/simulate",
            headers=ADMIN_HEADERS,
            json={"text": "what about warranty", "source": "active", "use_llm": False},
        )
    ).json()
    assert body["scenario"] != "warranty_query"


@pytest.mark.parametrize(
    "text",
    [
        "I am busy , camm me Tommorow at 5pm",  # the real message that was booked for today
        "I am busy, call me tomorow at 5 pm",
        "I am busy, call me tmrw 5pm",
        "I am busy, call me 2morrow at 5 pm",
        "I am busy, call me tomorrow at 5 pm",
    ],
)
def test_misspelt_tomorrow_still_means_tomorrow(text: str) -> None:
    result = classify_with_rules(text, state=None, today=TODAY, config=default_config())
    assert result is not None and result.scenario in ("busy_callback", "callback_request")
    assert result.facts["callback_date"] == "2026-10-16"  # TODAY is 15 Oct
    assert result.facts["callback_time_hhmm"] == "17:00"


@pytest.mark.parametrize("text", ["I will borrow it later, busy now", "no sorrow, I am busy at 5 pm"])
def test_words_that_only_look_like_tomorrow_do_not_move_the_date(text: str) -> None:
    result = classify_with_rules(text, state=None, today=TODAY, config=default_config())
    assert result is not None
    assert result.facts.get("callback_date", "2026-10-15") == "2026-10-15"


async def test_low_confidence_setting_is_versioned_and_validated(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    scenarios = [s.to_json() for s in DEFAULT_SCENARIOS]
    bad = await client.put(
        "/conversation-engine/config/draft",
        headers=ADMIN_HEADERS,
        json={"scenarios": scenarios, "settings": {"low_confidence_action": "explode"}},
    )
    assert bad.status_code == 422
    ok = await client.put(
        "/conversation-engine/config/draft",
        headers=ADMIN_HEADERS,
        json={"scenarios": scenarios, "settings": {"low_confidence_action": "clarify"}},
    )
    assert ok.status_code == 200 and ok.json()["settings"] == {"low_confidence_action": "clarify"}
    await client.post("/conversation-engine/config/draft/publish", headers=ADMIN_HEADERS, json={})
    live = (await client.get("/conversation-engine/config", headers=ADMIN_HEADERS)).json()
    assert live["active"]["settings"]["low_confidence_action"] == "clarify"
    # A later version can change it back; the earlier version keeps its own value.
    await client.put(
        "/conversation-engine/config/draft",
        headers=ADMIN_HEADERS,
        json={"scenarios": scenarios, "settings": {"low_confidence_action": "handoff"}},
    )
    await client.post("/conversation-engine/config/draft/publish", headers=ADMIN_HEADERS, json={})
    v1 = (await client.get("/conversation-engine/config/versions/1", headers=ADMIN_HEADERS)).json()
    v2 = (await client.get("/conversation-engine/config/versions/2", headers=ADMIN_HEADERS)).json()
    assert v1["settings"]["low_confidence_action"] == "clarify"
    assert v2["settings"]["low_confidence_action"] == "handoff"


# --- blueprint section 22: POST /internal/conversation/turn -----------------------------------------------

TURN_FIELDS = {
    "conversation_id", "agent_version_id", "agent_version", "response_text", "scenario", "state",
    "intent", "confidence", "memory_updates", "actions", "fallback_required", "fallback_reason",
}


async def _conv(db_session: AsyncSession) -> Conversation:
    from sqlalchemy import select as _select

    return (await db_session.execute(_select(Conversation))).scalars().first()


async def test_s22_turn_contract_returns_response_and_structured_actions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    conversation = await _conv(db_session)
    before = await db_session.scalar(select(func.count()).select_from(Appointment))
    response = await client.post(
        "/internal/conversation/turn",
        headers=ADMIN_HEADERS,
        json={
            "conversation_id": str(conversation.id),
            "customer_transcript": "I am busy, call me tomorrow at 5 pm",
            "call_context": {"channel": "voice"},
            "timestamp": "2026-10-16T10:00:00Z",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == TURN_FIELDS
    assert body["scenario"] == "busy_callback" and body["state"] == "callback_set"
    assert body["fallback_required"] is False and body["response_text"]
    assert body["actions"][0]["id"] == "request_callback"
    assert body["actions"][0]["args"]["time"] == "17:00"
    assert body["memory_updates"]["callback_time"].endswith("17:00")
    assert body["confidence"] > 0.5 and body["intent"]
    # The orchestrator decides; it does not execute: no callback was booked by this call.
    after = await db_session.scalar(select(func.count()).select_from(Appointment))
    assert after == before
    # Memory and the turn log were persisted, so the decision is traceable.
    turn = (await db_session.execute(select(ConversationTurn))).scalars().one()
    assert turn.mode == "api" and turn.channel == "voice" and turn.scenario == "busy_callback"


async def test_s22_turn_carries_state_between_calls(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    conversation = await _conv(db_session)

    async def turn(text: str) -> dict[str, Any]:
        r = await client.post(
            "/internal/conversation/turn", headers=ADMIN_HEADERS,
            json={"conversation_id": str(conversation.id), "customer_transcript": text},
        )
        assert r.status_code == 200, r.text
        return r.json()

    first = await turn("Haan bataiye, lekin main abhi thoda busy hoon.")
    assert first["state"] == "awaiting_callback_time" and first["actions"] == []
    second = await turn("Shaam ko 6 baje.")
    assert second["actions"][0]["id"] == "request_callback"
    assert second["actions"][0]["args"]["time"] == "18:00"  # the blueprint's section 29 example


async def test_s22_turn_asks_for_the_script_when_the_engine_is_off(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session, mode="strict_script")
    conversation = await _conv(db_session)
    body = (
        await client.post(
            "/internal/conversation/turn", headers=ADMIN_HEADERS,
            json={"conversation_id": str(conversation.id), "customer_transcript": "hello"},
        )
    ).json()
    assert body["fallback_required"] is True and body["response_text"] is None
    assert body["fallback_reason"] == "engine_off"


async def test_s22_turn_is_tenant_scoped_and_validates_versions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    other_user = uuid.uuid4()
    db_session.add(User(id=other_user, org_id=OTHER_ORG_ID, phone="+910000000077", name="O"))
    await db_session.commit()
    foreign = Conversation(org_id=OTHER_ORG_ID, user_id=other_user, channel="whatsapp")
    db_session.add(foreign)
    await db_session.commit()
    denied = await client.post(
        "/internal/conversation/turn", headers=ADMIN_HEADERS,
        json={"conversation_id": str(foreign.id), "customer_transcript": "hello"},
    )
    assert denied.status_code == 404  # another tenant's conversation is invisible
    mine = await _conv(db_session)
    bad_version = await client.post(
        "/internal/conversation/turn", headers=ADMIN_HEADERS,
        json={"conversation_id": str(mine.id), "agent_version_id": str(uuid.uuid4()), "customer_transcript": "hello"},
    )
    assert bad_version.status_code == 404
    assert (await client.post("/internal/conversation/turn", json={})).status_code in (401, 403, 422)


async def test_s22_turn_can_be_pinned_to_a_published_version(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    conversation = await _conv(db_session)
    scenarios = [s.to_json() for s in DEFAULT_SCENARIOS]
    await client.put("/conversation-engine/config/draft", headers=ADMIN_HEADERS, json={"scenarios": scenarios})
    published = (
        await client.post("/conversation-engine/config/draft/publish", headers=ADMIN_HEADERS, json={})
    ).json()
    body = (
        await client.post(
            "/internal/conversation/turn", headers=ADMIN_HEADERS,
            json={"conversation_id": str(conversation.id), "agent_version_id": published["id"],
                  "customer_transcript": "hello"},
        )
    ).json()
    assert body["agent_version_id"] == published["id"] and body["agent_version"] == 1


async def test_s13_structure_endpoint_is_read_only_and_org_scoped(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.api.db.models.script import Script
    from apps.api.routers import conversation_engine_config as router_module

    await _seed(db_session)
    mine = Script(org_id=ORG_ID, name="Main script", content="PRICING\nStarter is Rs 999 per month.", channel="whatsapp")
    theirs = Script(org_id=OTHER_ORG_ID, name="Their script", content="SECRET\nTheir plan is Rs 1.", channel="voice")
    db_session.add_all([mine, theirs])
    await db_session.commit()

    async def fake_chat(messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        body = json.dumps({"knowledge": [{"kind": "pricing", "title": "Starter", "body": "Starter is Rs 999 per month.", "keywords": "price"}],
                           "scenarios": []})
        return ChatResult(content=body, tokens_in=1, tokens_out=1)

    monkeypatch.setattr(router_module, "chat_completion", fake_chat)

    listed = (await client.get("/conversation-engine/scripts", headers=ADMIN_HEADERS)).json()
    assert [s["name"] for s in listed] == ["Main script"]  # the other org's script is invisible

    result = await client.post("/conversation-engine/script/structure", headers=ADMIN_HEADERS, json={"script_id": str(mine.id)})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["source"] == "Main script" and body["model_used"] is True, body["dropped"]
    assert [k["title"] for k in body["knowledge"]] == ["Starter"] and body["sections"][0]["title"] == "PRICING"

    assert (
        await client.post("/conversation-engine/script/structure", headers=ADMIN_HEADERS, json={"script_id": str(theirs.id)})
    ).status_code == 404
    assert (await client.post("/conversation-engine/script/structure", headers=ADMIN_HEADERS, json={})).status_code == 422

    # Pasted text without the model returns sections only.
    plain = (
        await client.post("/conversation-engine/script/structure", headers=ADMIN_HEADERS,
                          json={"text": "OPENING\nHello there", "use_llm": False})
    ).json()
    assert plain["model_used"] is False and plain["sections"][0]["kind"] == "opening"

    # Read-only: nothing was saved and the script is untouched.
    from apps.api.db.models.conversation_engine import KnowledgeItem as _K

    assert await db_session.scalar(select(func.count()).select_from(_K)) == 0
    await db_session.refresh(mine)
    assert mine.content == "PRICING\nStarter is Rs 999 per month."


# --- platform scenario templates: library management and independent copies -----------------------------


async def test_template_library_crud_and_validation(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    template = _scenario(id="returns_policy", name="Returns policy", examples=["can I return it", "return kaise karun"],
                         goal="Explain returns using approved facts")
    assert (await client.get("/conversation-engine/templates", headers=ADMIN_HEADERS)).json() == []

    created = await client.put("/conversation-engine/templates/returns_policy", headers=ADMIN_HEADERS, json=template)
    assert created.status_code == 200, created.text
    assert created.json()["scenario_id"] == "returns_policy"
    listed = (await client.get("/conversation-engine/templates", headers=ADMIN_HEADERS)).json()
    assert [t["scenario_id"] for t in listed] == ["returns_policy"]

    renamed = await client.put(
        "/conversation-engine/templates/returns_policy", headers=ADMIN_HEADERS, json={**template, "name": "Returns"}
    )
    assert renamed.status_code == 200 and renamed.json()["definition"]["name"] == "Returns"
    assert len((await client.get("/conversation-engine/templates", headers=ADMIN_HEADERS)).json()) == 1  # updated in place

    # Guard rails.
    mismatch = await client.put("/conversation-engine/templates/other_id", headers=ADMIN_HEADERS, json=template)
    assert mismatch.status_code == 422
    builtin = _scenario(id="greeting", name="Greeting")
    assert (await client.put("/conversation-engine/templates/greeting", headers=ADMIN_HEADERS, json=builtin)).status_code == 422
    no_examples = _scenario(id="empty_one", name="Empty", examples=[])
    assert (await client.put("/conversation-engine/templates/empty_one", headers=ADMIN_HEADERS, json=no_examples)).status_code == 422
    assert (await client.put("/conversation-engine/templates/returns_policy", json=template)).status_code in (401, 403)

    audit = [e["action"] for e in (await client.get("/conversation-engine/audit", headers=ADMIN_HEADERS)).json()]
    assert "template.created" in audit and "template.updated" in audit

    assert (await client.delete("/conversation-engine/templates/returns_policy", headers=ADMIN_HEADERS)).status_code == 204
    assert (await client.delete("/conversation-engine/templates/returns_policy", headers=ADMIN_HEADERS)).status_code == 404
    assert (await client.get("/conversation-engine/templates", headers=ADMIN_HEADERS)).json() == []


async def test_template_copies_are_independent_of_later_template_edits(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    template = _scenario(id="returns_policy", name="Returns policy", examples=["can I return it"], goal="Explain returns")
    await client.put("/conversation-engine/templates/returns_policy", headers=ADMIN_HEADERS, json=template)

    # An org copies the template into its draft and publishes it.
    copied = (await client.get("/conversation-engine/templates", headers=ADMIN_HEADERS)).json()[0]["definition"]
    await client.put(
        "/conversation-engine/config/draft", headers=ADMIN_HEADERS,
        json=_payload({k: v for k, v in copied.items() if k != "builtin"}),
    )
    assert (
        await client.post("/conversation-engine/config/draft/publish", headers=ADMIN_HEADERS, json={})
    ).status_code == 200

    # The platform admin later edits and then deletes the template.
    await client.put(
        "/conversation-engine/templates/returns_policy", headers=ADMIN_HEADERS,
        json={**template, "goal": "A completely different goal"},
    )
    await client.delete("/conversation-engine/templates/returns_policy", headers=ADMIN_HEADERS)

    live = (await client.get("/conversation-engine/config", headers=ADMIN_HEADERS)).json()["active"]
    mine = next(s for s in live["scenarios"] if s["id"] == "returns_policy")
    assert mine["goal"] == "Explain returns"  # the org's live copy did not change


def test_template_migration_is_a_single_additive_head() -> None:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    script = ScriptDirectory.from_config(config)
    # One head: the deploy's `alembic upgrade head` is unambiguous. (Not pinned to this
    # migration's id: any later additive migration legitimately becomes the head.)
    assert len(script.get_heads()) == 1
    assert script.get_revision("d0f6b4c8e2a5").down_revision == "c9e5a3b7d1f4"
