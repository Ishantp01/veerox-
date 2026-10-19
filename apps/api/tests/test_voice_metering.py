"""Phase 5: measured provider usage for voice (channels/voice/metering.py), recorded through
the existing usage ledger. Every figure comes from a provider report or from what we sent; the
tests also pin down what is NOT done: no estimating, no pricing, no double counting.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from apps.api.channels.voice import adapter, elevenlabs_client, recovery, turn_controller, voice_preview
from apps.api.channels.voice.metering import CallUsage, flush_call_usage
from apps.api.config import settings
from apps.api.core.conversation import voice_decision
from apps.api.core.conversation.types import Decision, Understanding
from apps.api.core.conversation.voice_decision import VoicePlan, _meter_decision
from apps.api.core.llm import ChatResult
from apps.api.db.models import Org, UsageEvent
from apps.api.tests.test_voice_tts_recovery import Call as RecoveryCall
from apps.api.tests.test_voice_tts_recovery import RSession

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER = uuid.UUID("00000000-0000-0000-0000-000000000ccc")


def response(id_: str = "resp_1", **usage: Any) -> dict[str, Any]:
    return {"id": id_, "status": "completed", "usage": usage}


FULL = {
    "input_tokens": 120,
    "output_tokens": 80,
    "input_token_details": {"audio_tokens": 100, "text_tokens": 20, "cached_tokens": 30},
    "output_token_details": {"audio_tokens": 0, "text_tokens": 80},
}


# --- reading the provider's report -----------------------------------------------------------------------------------------


def test_realtime_tokens_are_read_from_the_response_report() -> None:
    usage = CallUsage()
    assert usage.add_response(response(**FULL))
    assert (usage.rt_input_audio, usage.rt_input_text, usage.rt_input_cached) == (100, 20, 30)
    assert (usage.rt_output_audio, usage.rt_output_text) == (0, 80)
    assert usage.realtime_input_tokens == 120 and usage.realtime_output_tokens == 80


def test_a_response_is_counted_once_however_often_it_is_reported() -> None:
    usage = CallUsage()
    assert usage.add_response(response("r1", **FULL)) is True
    assert usage.add_response(response("r1", **FULL)) is False  # a duplicated event, or one replayed after a reconnect
    assert usage.add_response(response("r2", **FULL)) is True
    assert usage.realtime_input_tokens == 240 and len(usage.counted_responses) == 2


def test_an_interrupted_response_is_billed_so_it_is_counted() -> None:
    usage = CallUsage()
    cancelled = response("r1", **FULL)
    cancelled["status"] = "cancelled"
    assert usage.add_response(cancelled)
    assert usage.realtime_output_tokens == 80  # the provider generated (and bills) them


def test_a_response_without_usage_is_not_estimated() -> None:
    usage = CallUsage()
    assert usage.add_response({"id": "r1", "status": "completed"}) is False
    assert usage.add_response({"id": "r2", "usage": {}}) is False
    assert usage.add_response(None) is False
    assert usage.realtime_input_tokens == 0 and usage.responses_without_usage == 2  # recorded as unknown, not guessed


def test_garbage_in_the_report_cannot_corrupt_the_totals() -> None:
    usage = CallUsage()
    usage.add_response(response("r1", input_tokens="many", output_tokens=-5,
                                input_token_details={"audio_tokens": None, "cached_tokens": "x"},
                                output_token_details="nope"))
    assert usage.realtime_input_tokens == 0 and usage.realtime_output_tokens == 0


def test_transcription_seconds_come_from_the_audio_actually_streamed() -> None:
    usage = CallUsage()
    usage.transcribe_bytes = 8000 * 90 + 4000
    assert usage.transcribe_seconds == 90.5


def test_retried_tts_characters_are_kept_separate() -> None:
    usage = CallUsage()
    usage.add_tts_chars(120)
    usage.add_tts_chars(40, retry=True)
    usage.add_tts_chars(-3)
    usage.add_tts_chars(0)
    assert (usage.tts_chars, usage.tts_retry_chars) == (120, 40)


# --- picking it up in a live call ----------------------------------------------------------------------------------------------


class _Phone:
    async def send_text(self, message: str) -> None:
        return None

    async def close(self) -> None:
        return None


class _Agent:
    async def send(self, message: str) -> None:
        return None


class _Log:
    def info(self, *a: Any, **k: Any) -> None:
        return None

    warning = info


async def test_response_done_events_feed_the_calls_usage() -> None:
    state = adapter.CallState(user_id=USER, conversation_id=uuid.uuid4(), org_id=ORG_A)
    for rid in ("r1", "r1", "r2"):
        await adapter.handle_openai_event({"type": "response.done", "response": response(rid, **FULL)},
                                          _Phone(), _Agent(), state, _Log())  # type: ignore[arg-type]
    assert state.usage.realtime_input_tokens == 240  # r1 once, r2 once


async def test_tts_characters_are_counted_when_a_session_closes_and_retries_are_labelled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_synth(text: str, voice: Any = None) -> list[str]:
        return []

    RSession.instances = []
    RSession.script = [{"open_error": OSError("refused")}, {"audio": ["QQ=="]}]
    monkeypatch.setattr(elevenlabs_client, "ElevenLabsTTSSession", RSession)
    monkeypatch.setattr(recovery, "synthesize_greeting", no_synth)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.setattr(settings, "voice_tts_recovery", True)
    monkeypatch.setattr(settings, "voice_tts_first_audio_timeout_seconds", 30.0)
    call = RecoveryCall()
    await call.event("response.created", response={"id": "r1"})
    await call.event("response.text.delta", delta="Namaste ji", response_id="r1")
    await call.settle()
    await adapter.cancel_pending_tasks(call.state)  # closes the retry session -> reports its characters
    assert call.state.usage.tts_chars == 0 and call.state.usage.tts_retry_chars == len("Namaste ji")


async def test_the_turn_judges_tokens_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_chat(*a: Any, **k: Any) -> ChatResult:
        return ChatResult(content='{"label": "QUESTION", "confidence": 0.9}', tokens_in=41, tokens_out=7)

    monkeypatch.setattr("apps.api.core.llm.chat_completion", fake_chat)
    judged = await turn_controller.judge_turn("kitna lagega", None)
    assert judged is not None and (judged.tokens_in, judged.tokens_out) == (41, 7)
    assert judged == turn_controller.TurnJudgement("QUESTION", 0.9)  # tokens do not change the verdict's identity
    state = adapter.CallState(user_id=USER, conversation_id=uuid.uuid4(), org_id=ORG_A)
    state.utterance_text, state.cut_eval_deadline = "kitna lagega", 10**12
    await adapter._live_llm_check(_Agent(), _Phone(), state, _Log())  # type: ignore[arg-type]
    assert (state.usage.judge_in, state.usage.judge_out) == (41, 7)


# --- writing it down ---------------------------------------------------------------------------------------------------------------


@pytest.fixture
async def ledger(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    db_session.add_all([Org(id=ORG_A, name="A"), Org(id=ORG_B, name="B")])
    await db_session.commit()
    monkeypatch.setattr(settings, "voice_metering_v2", True)
    return db_session


def _usage() -> CallUsage:
    usage = CallUsage()
    usage.add_response(response("r1", **FULL))
    usage.transcribe_bytes = 8000 * 60
    usage.add_tts_chars(500)
    usage.add_tts_chars(80, retry=True)
    usage.add_judge(30, 6)
    usage.reconnects = 1
    return usage


async def _rows(db: Any) -> list[UsageEvent]:
    return list((await db.execute(select(UsageEvent).order_by(UsageEvent.usage_type, UsageEvent.source))).scalars())


async def test_a_call_writes_its_measured_usage_once(ledger: Any) -> None:
    written = await flush_call_usage(ledger, org_id=ORG_A, call_uuid="call-1", provider="plivo", usage=_usage())
    await ledger.commit()
    assert sorted(written) == sorted(
        ["ai_input_tokens", "ai_output_tokens", "transcribe_seconds", "tts_characters", "ai_input_tokens", "ai_output_tokens"]
    )
    by_source = {(r.source, r.usage_type): r for r in await _rows(ledger)}
    realtime_in = by_source[("voice_realtime", "ai_input_tokens")]
    assert (realtime_in.quantity, realtime_in.unit, realtime_in.provider) == (Decimal("120"), "tokens", "openai")
    assert by_source[("voice_realtime", "ai_output_tokens")].quantity == Decimal("80")
    assert by_source[("voice_live_transcribe", "transcribe_seconds")].quantity == Decimal("60.0")
    tts = by_source[("voice_tts", "tts_characters")]
    assert (tts.quantity, tts.unit, tts.provider) == (Decimal("580"), "characters", "elevenlabs")
    assert tts.metadata_json["retry_characters"] == 80  # billed, but visible as a retry
    assert by_source[("voice_turn_judge", "ai_input_tokens")].quantity == Decimal("30")
    assert {r.org_id for r in await _rows(ledger)} == {ORG_A} and {r.request_id for r in await _rows(ledger)} == {"call-1"}


async def test_flushing_twice_cannot_double_count(ledger: Any) -> None:
    usage = _usage()
    await flush_call_usage(ledger, org_id=ORG_A, call_uuid="call-1", provider="plivo", usage=usage)
    await ledger.commit()
    again = await flush_call_usage(ledger, org_id=ORG_A, call_uuid="call-1", provider="plivo", usage=usage)
    await ledger.commit()
    assert again == [] and len(await _rows(ledger)) == 6


async def test_everything_recorded_is_labelled_measured_and_carries_no_price(ledger: Any) -> None:
    await flush_call_usage(ledger, org_id=ORG_A, call_uuid="call-1", provider="plivo", usage=_usage())
    await ledger.commit()
    for row in await _rows(ledger):
        assert row.metadata_json["basis"] == "measured"
        assert not any("cost" in k or "price" in k or "estimate" in k for k in row.metadata_json)
        assert row.service == "ai"


async def test_two_calls_two_orgs_stay_separate(ledger: Any) -> None:
    await flush_call_usage(ledger, org_id=ORG_A, call_uuid="call-1", provider="plivo", usage=_usage())
    await flush_call_usage(ledger, org_id=ORG_B, call_uuid="call-2", provider="twilio", usage=_usage())
    await ledger.commit()
    rows = await _rows(ledger)
    assert len(rows) == 12
    assert {(r.org_id, r.request_id) for r in rows} == {(ORG_A, "call-1"), (ORG_B, "call-2")}


async def test_nothing_unmeasured_is_written(ledger: Any) -> None:
    written = await flush_call_usage(ledger, org_id=ORG_A, call_uuid="call-1", provider="plivo", usage=CallUsage())
    await ledger.commit()
    assert written == [] and await _rows(ledger) == []  # a call with no report records no tokens, not a guess


async def test_the_flag_off_writes_nothing(ledger: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_metering_v2", False)
    assert await flush_call_usage(ledger, org_id=ORG_A, call_uuid="call-1", provider="plivo", usage=_usage()) == []
    assert await flush_call_usage(ledger, org_id=ORG_A, call_uuid="", provider="plivo", usage=_usage()) == []
    await ledger.commit()
    assert await _rows(ledger) == []


# --- decision-layer tokens ---------------------------------------------------------------------------------------------------------


def _plan(tokens_in: int, tokens_out: int) -> VoicePlan:
    return VoicePlan(
        Understanding("interested", "x", "neutral", "en", 0.9, "llm", tokens_in=tokens_in, tokens_out=tokens_out),
        Decision("none", {}, "interested"), {}, None, 0.0,
    )


async def test_the_decision_layers_own_model_call_is_metered_once_per_decision(ledger: Any) -> None:
    plan = _plan(55, 12)
    conversation = uuid.uuid4()
    await _meter_decision(ledger, ORG_A, conversation, plan)
    await _meter_decision(ledger, ORG_A, conversation, plan)  # applied twice -> still one record
    rows = await _rows(ledger)
    assert {(r.usage_type, r.quantity, r.source) for r in rows} == {
        ("ai_input_tokens", Decimal("55"), "voice_decision"),
        ("ai_output_tokens", Decimal("12"), "voice_decision"),
    }
    await _meter_decision(ledger, ORG_A, conversation, _plan(10, 0))  # a different decision is a different record
    assert len(await _rows(ledger)) == 3


async def test_a_rules_only_decision_used_no_model_and_records_nothing(ledger: Any) -> None:
    await _meter_decision(ledger, ORG_A, uuid.uuid4(), _plan(0, 0))
    assert await _rows(ledger) == []


# --- previews ---------------------------------------------------------------------------------------------------------------------------


async def test_a_voice_preview_records_the_characters_it_sent(client: AsyncClient, ledger: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger.add(Org(id=uuid.UUID(settings.default_org_id), name="Default")) if uuid.UUID(settings.default_org_id) not in (ORG_A, ORG_B) else None
    await ledger.commit()

    async def synth(text: str, voice: Any) -> Any:
        return SimpleNamespace(audio=b"\xff" * 160, first_audio_ms=321)

    monkeypatch.setattr(voice_preview, "synthesize", synth)
    monkeypatch.setattr(elevenlabs_client, "is_configured", lambda: True)
    monkeypatch.setattr(voice_preview.preview_limiter, "allow", lambda org, limit: True)
    headers = {"X-Admin-Token": settings.admin_token}
    response_ = await client.post("/admin/settings/voice-profile/preview", json={"text": "Namaste, kaise hain aap?"}, headers=headers)
    assert response_.status_code == 200, response_.text
    rows = await _rows(ledger)
    (row,) = [r for r in rows if r.source == "voice_preview"]
    assert (row.usage_type, row.quantity, row.unit, row.provider) == ("tts_characters", Decimal("24"), "characters", "elevenlabs")
    assert row.metadata_json["basis"] == "measured"


async def test_a_failed_preview_records_nothing(client: AsyncClient, ledger: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def synth(text: str, voice: Any) -> Any:
        raise voice_preview.PreviewFailed("provider said no")

    monkeypatch.setattr(voice_preview, "synthesize", synth)
    monkeypatch.setattr(elevenlabs_client, "is_configured", lambda: True)
    monkeypatch.setattr(voice_preview.preview_limiter, "allow", lambda org, limit: True)
    result = await client.post("/admin/settings/voice-profile/preview", json={"text": "hello"},
                               headers={"X-Admin-Token": settings.admin_token})
    assert result.status_code == 502 and [r for r in await _rows(ledger) if r.source == "voice_preview"] == []
