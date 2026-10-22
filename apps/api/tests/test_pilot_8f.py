"""Phase 8F: reconnect-history honesty, a provider-enforced call duration, and a one-call guard for the pilot.

Offline: fake sockets, MockTransport, SQLite. The provider parameter names come from Plivo's published Make-a-call
documentation; whether Plivo then really ends a call at that moment is only provable on a live call.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from apps.api import pilot
from apps.api.channels.voice import adapter, failover, plivo_client, twilio_client
from apps.api.config import settings
from apps.api.core.org_credentials import PlivoCredentials, TwilioCredentials
from apps.api.tests.test_voice_reconnect import ORG_A, USER_A, _texts, history, state_for  # noqa: F401  (fixture)

PLIVO = PlivoCredentials(auth_id="AID", auth_token="tok")
TWILIO = TwilioCredentials(account_sid="ACx", auth_token="tok")


# --- reconnect history: nothing invented, nothing lost --------------------------------------------------------------------------


async def test_a_placeholder_for_an_untranscribed_caller_turn_is_never_replayed(history: SimpleNamespace) -> None:
    await history.add(history.a, "user", adapter.NO_TRANSCRIPT_PLACEHOLDER, 1)  # e.g. the greeting turn
    await history.add(history.a, "assistant", "Namaste, main AI assistant bol raha hoon.", 2)
    await history.add(history.a, "user", "Mera budget paanch lakh hai", 3)
    await history.add(history.a, "assistant", "Ji, paanch lakh.", 4)
    items = await adapter.build_reconnect_items(state_for(ORG_A, USER_A, history.a.id))
    assert [t for _, t in _texts(items)] == ["Namaste, main AI assistant bol raha hoon.", "Mera budget paanch lakh hai", "Ji, paanch lakh."]
    assert all("(voice)" not in t for _, t in _texts(items))


async def test_an_assistant_line_matching_the_placeholder_is_not_dropped_as_a_caller_turn(history: SimpleNamespace) -> None:
    await history.add(history.a, "assistant", adapter.NO_TRANSCRIPT_PLACEHOLDER, 1)
    assert len(await adapter.build_reconnect_items(state_for(ORG_A, USER_A, history.a.id))) == 1


async def test_the_callers_unanswered_last_words_are_restored_once(history: SimpleNamespace) -> None:
    await history.add(history.a, "user", "Namaste", 1)
    await history.add(history.a, "assistant", "Namaste, bataiye?", 2)
    state = state_for(ORG_A, USER_A, history.a.id)
    state.pending_user_transcript = "Investment kitna lagega, mera number 9876543210 hai"  # said, not answered, not saved
    items = await adapter.build_reconnect_items(state)
    assert _texts(items)[-1][0] == "user"
    last = _texts(items)[-1][1]
    assert "Investment kitna lagega" in last and "9876543210" not in last  # restored, and masked like everything else
    assert len(items) == 3


async def test_a_pending_transcript_that_is_already_saved_is_not_duplicated(history: SimpleNamespace) -> None:
    await history.add(history.a, "user", "Investment kitna lagega", 1)
    state = state_for(ORG_A, USER_A, history.a.id)
    state.pending_user_transcript = "Investment kitna lagega"
    assert len(await adapter.build_reconnect_items(state)) == 1


async def test_nothing_is_invented_when_there_is_no_history_and_no_pending_words(history: SimpleNamespace) -> None:
    state = state_for(ORG_A, USER_A, history.a.id)
    state.pending_user_transcript = None
    assert await adapter.build_reconnect_items(state) == []
    state.pending_user_transcript = adapter.NO_TRANSCRIPT_PLACEHOLDER
    assert await adapter.build_reconnect_items(state) == []


async def test_consecutive_reconnects_rebuild_the_same_history_instead_of_growing_it(history: SimpleNamespace) -> None:
    await history.add(history.a, "user", "Namaste", 1)
    await history.add(history.a, "assistant", "Namaste, bataiye?", 2)
    state = state_for(ORG_A, USER_A, history.a.id)
    first = await adapter.build_reconnect_items(state)
    second = await adapter.build_reconnect_items(state)
    third = await adapter.build_reconnect_items(state)
    assert first == second == third and len(first) == 2


def test_the_resume_instruction_names_completed_actions_and_nothing_the_caller_said() -> None:
    state = state_for(ORG_A, USER_A, None)
    state.completed_actions = ["book_appointment", "book_appointment", "request_callback"]
    state.pending_user_transcript = "SECRET-CALLER-WORDS"
    text = adapter.reconnect_resume_instruction(state)
    assert "book_appointment, request_callback" in text and "SECRET-CALLER-WORDS" not in text


# --- a provider-enforced call duration ------------------------------------------------------------------------------------------------


def _capture(monkeypatch: pytest.MonkeyPatch, module: Any) -> list[Any]:
    sent: list[Any] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.headers.get("content-type", "").startswith("application/json"):
            sent.append(json.loads(req.content))
        else:
            sent.append(dict(httpx.QueryParams(req.content.decode())))
        return httpx.Response(200, json={"request_uuid": "u1", "sid": "CA1"})

    monkeypatch.setattr(module, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return sent


async def test_by_default_the_provider_requests_carry_no_duration_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    plivo_sent, twilio_sent = _capture(monkeypatch, plivo_client), _capture(monkeypatch, twilio_client)
    await plivo_client.initiate_call(PLIVO, "+911234567890", "https://x/answer", "+910000000001")
    await twilio_client.initiate_call(TWILIO, "+911234567890", "https://x/answer", "+910000000001")
    assert settings.voice_outbound_time_limit_seconds == 0 and settings.voice_outbound_ring_timeout_seconds == 0
    assert set(plivo_sent[0]) == {"from", "to", "answer_url", "answer_method"}
    assert "TimeLimit" not in twilio_sent[0] and "Timeout" not in twilio_sent[0]


async def test_a_configured_limit_is_sent_to_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_outbound_time_limit_seconds", 120)
    monkeypatch.setattr(settings, "voice_outbound_ring_timeout_seconds", 25)
    plivo_sent, twilio_sent = _capture(monkeypatch, plivo_client), _capture(monkeypatch, twilio_client)
    await plivo_client.initiate_call(PLIVO, "+911234567890", "https://x/answer", "+910000000001")
    await twilio_client.initiate_call(TWILIO, "+911234567890", "https://x/answer", "+910000000001")
    assert plivo_sent[0]["time_limit"] == 120 and plivo_sent[0]["ring_timeout"] == 25
    assert isinstance(plivo_sent[0]["time_limit"], int)
    assert twilio_sent[0]["TimeLimit"] == "120" and twilio_sent[0]["Timeout"] == "25"


async def test_the_limit_applies_to_the_fallback_provider_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_outbound_time_limit_seconds", 90)
    twilio_sent = _capture(monkeypatch, twilio_client)

    async def plivo_down(*a: Any, **k: Any) -> dict[str, Any]:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(plivo_client, "initiate_call", plivo_down)
    _, provider = await failover.initiate_call(
        PLIVO, TWILIO, "+911234567890", "https://x/answer", plivo_from_number="+910000000001", twilio_from_number="+910000000002"
    )
    assert provider == "twilio" and twilio_sent[0]["TimeLimit"] == "90"


# --- the pilot's one-call guard ---------------------------------------------------------------------------------------------------------


def _pilot(monkeypatch: pytest.MonkeyPatch, max_calls: int) -> None:
    pilot.reset_outbound_counter()
    monkeypatch.setattr(settings, "environment", "pilot")
    monkeypatch.setattr(settings, "pilot_allowed_numbers", "+911234567890")
    monkeypatch.setattr(settings, "pilot_max_outbound_calls", max_calls)


async def _place(monkeypatch: pytest.MonkeyPatch, to: str = "+911234567890") -> list[str]:
    reached: list[str] = []

    async def provider(*a: Any, **k: Any) -> dict[str, Any]:
        reached.append("provider")
        return {"request_uuid": "u"}

    monkeypatch.setattr(plivo_client, "initiate_call", provider)
    await failover.initiate_call(PLIVO, None, to, "https://x/answer", plivo_from_number="+910000000001")
    return reached


async def test_a_one_call_pilot_refuses_the_second_call_before_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, 1)
    try:
        assert await _place(monkeypatch) == ["provider"]
        with pytest.raises(pilot.PilotBlocked, match="limit of 1"):
            await _place(monkeypatch)
    finally:
        pilot.reset_outbound_counter()


async def test_a_refused_number_does_not_use_up_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, 1)
    try:
        with pytest.raises(pilot.PilotBlocked, match="PILOT_ALLOWED_NUMBERS"):
            await _place(monkeypatch, to="+919999999999")
        assert await _place(monkeypatch) == ["provider"]  # the allowed call is still available
    finally:
        pilot.reset_outbound_counter()


async def test_zero_means_no_limit_and_other_environments_are_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, 0)
    try:
        for _ in range(3):
            await _place(monkeypatch)
        monkeypatch.setattr(settings, "environment", "dev")
        monkeypatch.setattr(settings, "pilot_max_outbound_calls", 1)
        for _ in range(3):
            await _place(monkeypatch)
    finally:
        pilot.reset_outbound_counter()


async def test_the_counter_resets_on_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, 1)
    await _place(monkeypatch)
    pilot.reset_outbound_counter()
    assert await _place(monkeypatch) == ["provider"]
    pilot.reset_outbound_counter()


# --- the preflight notices a paid-call pilot with no safeguards ------------------------------------------------------------------------------------

from apps.api.pilot_checks import check_pilot_env, parse_env

_BASE = parse_env(
    "ENVIRONMENT=pilot\nPILOT_DATABASE_NAME=neondb\nDATABASE_URL=postgresql+asyncpg://u:p@new.example.net/neondb\n"
    "REDIS_URL=rediss://d:p@new-r.example.net:6379\nPUBLIC_BASE_URL=https://p.ngrok-free.dev\nPILOT_ALLOWED_NUMBERS=+911234567890\n"
)


def _status(env: dict[str, str], check: str) -> str:
    return next(r.status for r in check_pilot_env(env, {}) if r.check == check)


def test_missing_call_safeguards_are_warned_about_and_set_ones_pass() -> None:
    assert _status(_BASE, "per-run outbound call limit") == "WARN" and _status(_BASE, "provider call-length limit") == "WARN"
    guarded = {**_BASE, "PILOT_MAX_OUTBOUND_CALLS": "1", "VOICE_OUTBOUND_TIME_LIMIT_SECONDS": "120"}
    assert _status(guarded, "per-run outbound call limit") == "PASS" and _status(guarded, "provider call-length limit") == "PASS"
    assert _status({**guarded, "VOICE_OUTBOUND_TIME_LIMIT_SECONDS": "0"}, "provider call-length limit") == "WARN"
