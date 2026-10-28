"""Phase 5: surviving an OpenAI Realtime reconnect without repeating business actions,
replaying sensitive history, or crossing organizations.

The reconnect loop itself lives inside the call handler; these tests cover the pieces it
is built from (budget decision, context replay, duplicate suppression, pre-connected
session ownership), which is where the correctness risks are.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from apps.api.channels.voice import adapter
from apps.api.channels.voice import realtime_bridge as bridge
from apps.api.channels.voice.compliance import RecordingPolicy
from apps.api.config import settings
from apps.api.db.models import Conversation, Message, Org, User

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER_A = uuid.UUID("00000000-0000-0000-0000-00000000aaaa")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000bbbb")


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def info(self, event: str, *a: Any, **k: Any) -> None:
        self.events.append(event)

    warning = info
    error = info


def state_for(org: uuid.UUID = ORG_A, user: uuid.UUID = USER_A, conversation: uuid.UUID | None = None) -> adapter.CallState:
    return adapter.CallState(user_id=user, conversation_id=conversation or uuid.uuid4(), org_id=org, call_uuid="call-1")


# --- how long to keep trying -----------------------------------------------------------------------------------------------


def test_the_attempt_cap_always_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_reconnect_recovery", False)
    assert not bridge.reconnect_exhausted(8, 10_000.0)  # the legacy rule ignores elapsed time
    assert bridge.reconnect_exhausted(9, 0.0)


def test_with_recovery_a_long_outage_stops_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_reconnect_recovery", True)
    monkeypatch.setattr(settings, "voice_reconnect_budget_seconds", 45.0)
    assert not bridge.reconnect_exhausted(2, 44.0)
    assert bridge.reconnect_exhausted(2, 46.0)  # slow attempts cannot keep a caller waiting for minutes
    assert bridge.reconnect_exhausted(9, 0.0)


def test_a_bad_budget_setting_falls_back_to_a_sane_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_reconnect_recovery", True)
    for junk in (float("nan"), "soon", None):
        monkeypatch.setattr(settings, "voice_reconnect_budget_seconds", junk)
        assert bridge.reconnect_exhausted(1, 50.0) and not bridge.reconnect_exhausted(1, 10.0)
    monkeypatch.setattr(settings, "voice_reconnect_budget_seconds", 0)  # cannot be switched off by zero
    assert not bridge.reconnect_exhausted(1, 4.0) and bridge.reconnect_exhausted(1, 6.0)


# --- no repeated business actions -------------------------------------------------------------------------------------------


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    seen = SimpleNamespace(calls=[], results={})

    async def fake(name: str, args_json: str, *a: Any, **k: Any) -> dict[str, Any]:
        seen.calls.append((name, args_json))
        return seen.results.get(name, {"status": "ok", "appointment_id": f"id-{len(seen.calls)}"})

    monkeypatch.setattr(adapter, "_dispatch_realtime_tool", fake)
    monkeypatch.setattr(settings, "voice_reconnect_recovery", True)
    return seen


async def test_the_same_booking_is_made_once(tools: SimpleNamespace) -> None:
    state, log = state_for(), _Log()
    first = await adapter._dispatch_deduped(state, "book_appointment", '{"date": "2026-10-20", "time": "17:00"}', log)
    again = await adapter._dispatch_deduped(state, "book_appointment", '{"time":"17:00","date":"2026-10-20"}', log)  # key order differs
    assert len(tools.calls) == 1
    assert again["duplicate"] is True and again["appointment_id"] == first["appointment_id"]
    assert state.completed_actions == ["book_appointment"] and "voice_tool_duplicate_suppressed" in log.events


async def test_a_genuinely_different_request_is_still_made(tools: SimpleNamespace) -> None:
    state, log = state_for(), _Log()
    await adapter._dispatch_deduped(state, "book_appointment", '{"date": "2026-10-20", "time": "17:00"}', log)
    await adapter._dispatch_deduped(state, "book_appointment", '{"date": "2026-10-21", "time": "10:00"}', log)
    assert len(tools.calls) == 2  # a rescheduled booking is a new action


async def test_a_failed_action_is_not_remembered_so_it_can_be_retried(tools: SimpleNamespace) -> None:
    state, log = state_for(), _Log()
    tools.results["book_appointment"] = {"status": "error", "reason": "slot_conflict"}
    await adapter._dispatch_deduped(state, "book_appointment", '{"date": "2026-10-20"}', log)
    tools.results["book_appointment"] = {"status": "ok"}
    retried = await adapter._dispatch_deduped(state, "book_appointment", '{"date": "2026-10-20"}', log)
    assert len(tools.calls) == 2 and "duplicate" not in retried


async def test_read_only_tools_are_never_suppressed(tools: SimpleNamespace) -> None:
    state, log = state_for(), _Log()
    for _ in range(3):
        await adapter._dispatch_deduped(state, "lookup_customer", "{}", log)
    assert len(tools.calls) == 3


async def test_with_the_flag_off_nothing_is_suppressed(tools: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_reconnect_recovery", False)
    state, log = state_for(), _Log()
    for _ in range(2):
        await adapter._dispatch_deduped(state, "book_appointment", '{"date": "2026-10-20"}', log)
    assert len(tools.calls) == 2 and state.completed_actions == []  # exactly the previous behaviour


async def test_completed_actions_survive_the_reconnect_reset(tools: SimpleNamespace) -> None:
    """The model of the NEW session may try the booking again; the call's record must still stop it."""
    state, log = state_for(), _Log()
    await adapter._dispatch_deduped(state, "send_whatsapp_message", '{"message": "details"}', log)
    adapter.reset_after_reconnect(state)
    await adapter._dispatch_deduped(state, "send_whatsapp_message", '{"message": "details"}', log)
    assert len(tools.calls) == 1


async def test_one_calls_actions_never_suppress_another_calls(tools: SimpleNamespace) -> None:
    first, second, log = state_for(ORG_A, USER_A), state_for(ORG_B, USER_B), _Log()
    args = '{"date": "2026-10-20", "time": "17:00"}'
    await adapter._dispatch_deduped(first, "book_appointment", args, log)
    await adapter._dispatch_deduped(second, "book_appointment", args, log)  # same request, other org
    assert len(tools.calls) == 2


async def test_unparseable_arguments_still_dedupe_on_the_raw_text(tools: SimpleNamespace) -> None:
    state, log = state_for(), _Log()
    await adapter._dispatch_deduped(state, "request_callback", "{broken", log)
    await adapter._dispatch_deduped(state, "request_callback", "{broken", log)
    assert len(tools.calls) == 1


def test_the_resume_instruction_says_what_not_to_redo() -> None:
    state = state_for()
    state.completed_actions = ["book_appointment", "book_appointment", "send_whatsapp_message"]
    text = adapter.reconnect_resume_instruction(state)
    assert "do NOT greet again" in text and "never do them again" in text
    assert text.count("book_appointment") == 1 and "send_whatsapp_message" in text
    assert "none" in adapter.reconnect_resume_instruction(state_for())


# --- replaying a little context, safely --------------------------------------------------------------------------------------


@pytest.fixture
async def history(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    db_session.add_all([Org(id=ORG_A, name="A"), Org(id=ORG_B, name="B")])
    db_session.add_all([User(id=USER_A, org_id=ORG_A, phone="+910000000001"), User(id=USER_B, org_id=ORG_B, phone="+910000000001")])
    conv_a = Conversation(org_id=ORG_A, user_id=USER_A, channel="voice")
    conv_a2 = Conversation(org_id=ORG_A, user_id=USER_A, channel="voice")
    conv_b = Conversation(org_id=ORG_B, user_id=USER_B, channel="voice")
    db_session.add_all([conv_a, conv_a2, conv_b])
    await db_session.commit()

    class _Ctx:
        async def __aenter__(self) -> Any:
            return db_session

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(adapter, "AsyncSessionLocal", lambda: _Ctx())
    monkeypatch.setattr(settings, "voice_reconnect_context_turns", 6)
    base = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)

    async def add(conv: Conversation, role: str, content: str, minute: int) -> None:
        db_session.add(Message(org_id=conv.org_id, conversation_id=conv.id, user_id=conv.user_id, role=role,
                               content=content, channel="voice", created_at=base + timedelta(minutes=minute)))
        await db_session.commit()

    return SimpleNamespace(a=conv_a, a2=conv_a2, b=conv_b, add=add)


def _texts(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(i["item"]["role"], i["item"]["content"][0]["text"]) for i in items]


async def test_only_this_calls_recent_turns_are_replayed_in_order(history: SimpleNamespace) -> None:
    await history.add(history.a, "user", "Namaste", 1)
    await history.add(history.a, "assistant", "Namaste, kaise madad karun?", 2)
    await history.add(history.a2, "user", "an earlier call, same customer", 3)  # another conversation
    await history.add(history.b, "user", "another org entirely", 4)
    items = await adapter.build_reconnect_items(state_for(ORG_A, USER_A, history.a.id))
    assert _texts(items) == [("user", "Namaste"), ("assistant", "Namaste, kaise madad karun?")]
    assert all(i["type"] == "conversation.item.create" and i["item"]["type"] == "message" for i in items)
    assert [i["item"]["content"][0]["type"] for i in items] == ["input_text", "output_text"]


async def test_another_orgs_conversation_id_yields_nothing(history: SimpleNamespace) -> None:
    await history.add(history.b, "user", "private to org B", 1)
    # An org A call can never read org B's turns, even if it somehow held B's conversation id.
    assert await adapter.build_reconnect_items(state_for(ORG_A, USER_A, history.b.id)) == []


async def test_only_the_last_few_turns_are_replayed(history: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    for minute in range(1, 11):
        await history.add(history.a, "user" if minute % 2 else "assistant", f"turn {minute}", minute)
    items = await adapter.build_reconnect_items(state_for(ORG_A, USER_A, history.a.id))
    assert [t for _, t in _texts(items)] == [f"turn {n}" for n in range(5, 11)]  # the last 6, oldest first
    monkeypatch.setattr(settings, "voice_reconnect_context_turns", 0)
    assert await adapter.build_reconnect_items(state_for(ORG_A, USER_A, history.a.id)) == []
    monkeypatch.setattr(settings, "voice_reconnect_context_turns", "lots")  # garbage -> the default bound
    assert len(await adapter.build_reconnect_items(state_for(ORG_A, USER_A, history.a.id))) == 6
    monkeypatch.setattr(settings, "voice_reconnect_context_turns", 10_000)  # and a hard ceiling
    assert len(await adapter.build_reconnect_items(state_for(ORG_A, USER_A, history.a.id))) == 10


async def test_sensitive_values_are_masked_and_long_turns_truncated(history: SimpleNamespace) -> None:
    await history.add(history.a, "user", "mera otp 482913 hai aur number 9876543210, mail asha@example.com", 1)
    await history.add(history.a, "assistant", "x" * 2000, 2)
    await history.add(history.a, "user", "   ", 3)  # empty turns are skipped
    items = await adapter.build_reconnect_items(state_for(ORG_A, USER_A, history.a.id))
    (_, first), (_, second) = _texts(items)
    assert "482913" not in first and "9876543210" not in first and "asha@example.com" not in first
    assert "[code]" in first and "[email]" in first
    assert len(second) == 500


async def test_no_tool_calls_are_ever_replayed(history: SimpleNamespace) -> None:
    await history.add(history.a, "user", "book kar do", 1)
    await history.add(history.a, "tool", '{"name": "book_appointment"}', 2)  # a stored tool row
    items = await adapter.build_reconnect_items(state_for(ORG_A, USER_A, history.a.id))
    assert {i["item"]["type"] for i in items} == {"message"}  # replaying these is how a booking happens twice


# --- a pre-connected session belongs to one org ----------------------------------------------------------------------------------


class _Oai:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[dict[str, Any]] = []

    async def close(self) -> None:
        self.closed = True

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


@pytest.fixture(autouse=True)
def _clean_precalls() -> Any:
    yield
    for task in bridge._pending_precalls.values():
        task.cancel()
    bridge._pending_precalls.clear()
    bridge._pending_precall_orgs.clear()


async def _register(call_uuid: str, org: uuid.UUID) -> _Oai:
    oai = _Oai()

    async def prepared() -> tuple[Any, str]:
        return oai, f"instructions for {org}"

    bridge._pending_precalls[call_uuid] = asyncio.create_task(prepared())
    bridge._pending_precall_orgs[call_uuid] = org
    return oai


async def test_a_session_prepared_for_another_org_is_discarded_not_reused() -> None:
    oai = await _register("c1", ORG_A)
    assert await bridge.claim_precall_connect("c1", ORG_B) is None
    assert oai.closed  # and the connection is closed, not leaked
    assert "c1" not in bridge._pending_precalls and "c1" not in bridge._pending_precall_orgs


async def test_a_session_for_the_same_org_is_handed_over() -> None:
    oai = await _register("c1", ORG_A)
    claimed = await bridge.claim_precall_connect("c1", ORG_A)
    assert claimed is not None and claimed[0] is oai and not oai.closed


async def test_claiming_without_an_expected_org_behaves_as_before() -> None:
    oai = await _register("c1", ORG_A)
    claimed = await bridge.claim_precall_connect("c1")
    assert claimed is not None and claimed[0] is oai


async def test_claiming_something_that_was_never_prepared_returns_nothing() -> None:
    assert await bridge.claim_precall_connect("nope", ORG_A) is None
    assert await bridge.claim_precall_connect("", ORG_A) is None


async def test_preparing_a_session_records_its_org_and_the_consent_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    oai = _Oai()

    async def instructions(*a: Any, **k: Any) -> str:
        return "INSTR"

    async def connect(org_id: Any = None) -> _Oai:
        return oai

    async def context(org_id: Any, remote: Any) -> tuple[str, RecordingPolicy]:
        return "Acme", RecordingPolicy(mode="consent")

    monkeypatch.setattr(bridge, "_system_instructions", instructions)
    monkeypatch.setattr(bridge, "_connect_to_openai", connect)
    monkeypatch.setattr(bridge, "_call_context", context)
    bridge.start_precall_connect("c9", None, ORG_B, remote_number="+919876543210")
    assert bridge._pending_precall_orgs["c9"] == ORG_B
    await bridge._pending_precalls["c9"]
    tools = [t["name"] for t in oai.sent[0]["session"]["tools"]]
    assert tools[-1] == "recording_consent"  # this org asked for consent on this caller's region
