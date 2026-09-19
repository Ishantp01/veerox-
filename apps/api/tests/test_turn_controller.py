"""Tests for live turn-taking: the filler/real classifier and the adapter's
cut-off decisions driven by streamed partial transcripts."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest

from apps.api.channels.voice import adapter, turn_controller
from apps.api.channels.voice.turn_controller import (
    TurnJudgement,
    classify_partial,
    endpoint_hold_seconds,
    is_backchannel,
    parse_judgement,
)


# ---------------------------------------------------------------------------
# classify_partial / is_backchannel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "haan",
        "Yeah, yeah, okay, got it.",
        "haan haan samajh gaya",
        "हाँ हाँ, ठीक है, समझ गया।",
        "ஆமா ஆமா, சரி, புரிந்தது.",  # Tamil fillers from the spike
        "అవును, సరే, అర్థమైంది",
        "lovely",
        "ok ok ok theek hai",
    ],
)
def test_fillers_are_classified_filler(text: str) -> None:
    assert classify_partial(text) == "filler"


@pytest.mark.parametrize(
    "text",
    [
        "haan lekin price kya hai",
        "Yeah, but how much does it cost",
        "हाँ, लेकिन इसकी कीमत कितनी है",
        "ruko ek second",
        "wait I have a question",
        "no I don't want that",
        "one two three four five six seven eight nine",  # long, no filler cues
    ],
)
def test_real_turns_are_classified_real(text: str) -> None:
    assert classify_partial(text) == "real"


@pytest.mark.parametrize("text", ["", "   ", "अवनो කानी"])
def test_unknown_text_is_unsure(text: str) -> None:
    assert classify_partial(text) == "unsure"


def test_question_mark_alone_is_not_proof_of_a_question() -> None:
    # The transcriber sprinkles "?" on fillers in some languages.
    assert classify_partial("haan ok?") == "filler"


def test_long_run_of_only_fillers_is_a_real_turn() -> None:
    assert classify_partial("haan " * 9) == "real"


def test_is_backchannel_keeps_old_behaviour() -> None:
    assert is_backchannel("ok")
    assert is_backchannel("")
    assert not is_backchannel("haan lekin price")
    assert not is_backchannel("ok ok ok ok ok")  # over the 4-word default


# ---------------------------------------------------------------------------
# handle_live_delta
# ---------------------------------------------------------------------------


class _FakeOai:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


class _FakeCall:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


class _Log:
    def info(self, *a: Any, **k: Any) -> None: ...
    def warning(self, *a: Any, **k: Any) -> None: ...


def _state(**overrides: Any) -> adapter.CallState:
    state = adapter.CallState(
        user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), org_id=uuid.uuid4()
    )
    state.response_active = True  # agent is mid-answer
    state.caller_speaking = True
    state.cut_eval_deadline = time.monotonic() + 60
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


async def _feed(state: adapter.CallState, oai: _FakeOai, call: _FakeCall, *deltas: str) -> None:
    for delta in deltas:
        await adapter.handle_live_delta(delta, call, oai, state, _Log())  # type: ignore[arg-type]


async def test_filler_does_not_cut_the_agent() -> None:
    state, oai, call = _state(), _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " haan", " haan", ",", " samajh", " gaya")
    assert oai.sent == []
    assert call.sent == []


async def test_real_question_cuts_the_agent_immediately() -> None:
    state, oai, call = _state(), _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " haan", ",", " lekin", " price")
    assert oai.types() == ["response.cancel"]
    assert call.sent == [{"event": "clearAudio"}]
    assert state.cut_eval_deadline == 0.0  # only cut once


async def test_no_cut_when_agent_is_not_speaking() -> None:
    state = _state(response_active=False)
    oai, call = _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " what", " is", " the", " price")
    assert oai.sent == [] and call.sent == []


async def test_no_cut_outside_the_judging_window() -> None:
    state = _state(cut_eval_deadline=0.0)
    oai, call = _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " what", " is", " the", " price")
    assert oai.sent == []


async def test_greeting_guard_blocks_the_cut() -> None:
    state = _state(greeting_guard_until=time.monotonic() + 10)
    oai, call = _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " what", " is", " the", " price")
    assert oai.sent == []


async def test_unsure_text_is_judged_by_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_llm(text: str, api_key: str | None, timeout: float = 2.5) -> TurnJudgement | None:
        calls.append(text)
        return TurnJudgement("QUESTION", 0.9)

    monkeypatch.setattr(turn_controller, "judge_turn", fake_llm)
    state, oai, call = _state(), _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " अवनो", " කානි")  # garbled, no cues -> unsure
    assert state.live_llm_task is not None
    await state.live_llm_task
    assert calls, "LLM was never consulted"
    assert oai.types() == ["response.cancel"]


async def test_llm_saying_filler_keeps_the_agent_talking(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_llm(text: str, api_key: str | None, timeout: float = 2.5) -> TurnJudgement | None:
        return TurnJudgement("FILLER", 0.95)

    monkeypatch.setattr(turn_controller, "judge_turn", fake_llm)
    state, oai, call = _state(), _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " ਵਧੀਆ", " ਜੀ")
    assert state.live_llm_task is not None
    await state.live_llm_task
    assert oai.sent == []
    assert state.live_llm_said_filler


async def test_llm_calls_are_capped_per_utterance(monkeypatch: pytest.MonkeyPatch) -> None:
    n = 0

    async def fake_llm(text: str, api_key: str | None, timeout: float = 2.5) -> TurnJudgement | None:
        nonlocal n
        n += 1
        return TurnJudgement("FILLER", 0.95)

    monkeypatch.setattr(turn_controller, "judge_turn", fake_llm)
    state, oai, call = _state(), _FakeOai(), _FakeCall()
    for word in [" ਇੱਕ", " ਦੋ", " ਤਿੰਨ", " ਚਾਰ", " ਪੰਜ", " ਛੇ"]:
        await _feed(state, oai, call, word)
        if state.live_llm_task:
            await state.live_llm_task
    assert n == adapter.LIVE_LLM_MAX_CALLS


async def test_filler_after_their_turn_ended_drops_the_pending_reply() -> None:
    state = _state(caller_speaking=False, reply_pending=True, restate_next=True)
    oai, call = _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " haan", " ji")
    assert state.reply_pending is False
    assert oai.sent == []


async def test_real_turn_after_their_speech_ended_still_gets_answered() -> None:
    # Their turn committed and its reply was already dropped as filler; more
    # text then reveals it was a real question.
    state = _state(caller_speaking=False, reply_pending=False)
    oai, call = _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " haan", " lekin", " price")
    assert state.reply_pending is True  # fires from the cancelled response's response.done
    assert oai.types() == ["response.cancel"]


async def test_cut_answers_immediately_when_audio_is_queued_but_generation_is_done() -> None:
    # Agent finished generating (response_active False) but its audio is still
    # playing at the provider: clear it and answer at once.
    state = _state(
        response_active=False,
        caller_speaking=False,
        reply_pending=True,
        playback_end=time.monotonic() + 5,
    )
    oai, call = _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " haan", " lekin", " price")
    await asyncio.sleep(0.05)  # _schedule_reply fires as a task
    assert call.sent == [{"event": "clearAudio"}]
    assert "response.create" in oai.types()
    assert "response.cancel" not in oai.types()


async def test_utterance_text_accumulates_even_when_not_judging() -> None:
    state = _state(cut_eval_deadline=0.0)
    await _feed(state, _FakeOai(), _FakeCall(), " hello", " there")
    assert state.utterance_text == " hello there"


# ---------------------------------------------------------------------------
# LiveTranscriber
# ---------------------------------------------------------------------------


async def test_feed_is_a_noop_until_healthy() -> None:
    async def on_delta(_: str) -> None: ...

    t = turn_controller.LiveTranscriber("key", on_delta, _Log())
    t.feed("AAAA")
    assert t._queue.qsize() == 0


async def test_start_without_key_fails_cleanly() -> None:
    async def on_delta(_: str) -> None: ...

    t = turn_controller.LiveTranscriber(None, on_delta, _Log())
    assert await t.start() is False
    assert t.healthy is False
    await t.close()  # safe to close a never-started transcriber


# ---------------------------------------------------------------------------
# LLM judgement: labels + confidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "confidence", "verdict"),
    [
        ("FILLER", 0.95, "filler"),
        ("QUESTION", 0.9, "real"),
        ("OBJECTION", 0.7, "real"),
        ("INTERRUPTION", 0.8, "real"),
        ("CONTINUATION", 0.9, "wait"),
        ("QUESTION", 0.4, "unsure"),  # low confidence: keep listening
        ("FILLER", 0.3, "unsure"),
    ],
)
def test_judgement_verdicts(label: str, confidence: float, verdict: str) -> None:
    assert TurnJudgement(label, confidence).verdict == verdict


def test_parse_judgement_accepts_json_with_noise_and_case() -> None:
    j = parse_judgement('Sure: {"label": "question", "confidence": 0.85}')
    assert j == TurnJudgement("QUESTION", 0.85)


@pytest.mark.parametrize(
    "content",
    [None, "", "FILLER", '{"label": "MAYBE", "confidence": 0.9}', '{"confidence": 0.9}', "{bad json}"],
)
def test_parse_judgement_rejects_garbage(content: str | None) -> None:
    assert parse_judgement(content) is None


def test_parse_judgement_clamps_confidence() -> None:
    assert parse_judgement('{"label": "FILLER", "confidence": 7}').confidence == 1.0


async def test_low_confidence_llm_verdict_does_not_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_llm(text: str, api_key: str | None, timeout: float = 2.5) -> TurnJudgement | None:
        return TurnJudgement("QUESTION", 0.3)

    monkeypatch.setattr(turn_controller, "judge_turn", fake_llm)
    state, oai, call = _state(), _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " ਵਧੀਆ", " ਜੀ")
    await state.live_llm_task  # type: ignore[misc]
    assert oai.sent == []


async def test_continuation_llm_verdict_does_not_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_llm(text: str, api_key: str | None, timeout: float = 2.5) -> TurnJudgement | None:
        return TurnJudgement("CONTINUATION", 0.9)

    monkeypatch.setattr(turn_controller, "judge_turn", fake_llm)
    state, oai, call = _state(), _FakeOai(), _FakeCall()
    await _feed(state, oai, call, " ਵਧੀਆ", " ਜੀ")
    await state.live_llm_task  # type: ignore[misc]
    assert oai.sent == []


# ---------------------------------------------------------------------------
# Adaptive endpointing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["haan, mera matlab", "haan…", "I want to know, and", "mujhe ek cheez poochni thi, lekin", "और"],
)
def test_unfinished_text_gets_a_hold(text: str) -> None:
    assert endpoint_hold_seconds(text, agent_was_busy=True) == turn_controller.HOLD_CONTINUATION_SECONDS


@pytest.mark.parametrize(
    "text",
    ["how much does it cost?", "haan mujhe pricing chahiye", "mera naam Rahul hai.", "", "   "],
)
def test_finished_or_empty_text_gets_no_hold(text: str) -> None:
    assert endpoint_hold_seconds(text, agent_was_busy=True) == 0.0


def test_lone_filler_to_an_idle_agent_gets_a_short_hold() -> None:
    assert endpoint_hold_seconds("haan", agent_was_busy=False) == turn_controller.HOLD_LONE_FILLER_SECONDS
    assert endpoint_hold_seconds("haan", agent_was_busy=True) == 0.0


async def test_reply_is_held_while_hold_is_active_then_sent() -> None:
    state = _state(response_active=False, caller_speaking=False, reply_pending=True)
    state.hold_until = time.monotonic() + 0.15
    oai = _FakeOai()
    adapter._schedule_reply(oai, state)  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    assert oai.sent == []  # still holding
    await asyncio.sleep(0.3)
    assert "response.create" in oai.types()


async def test_resumed_speech_cancels_the_hold() -> None:
    state = _state(response_active=False, caller_speaking=False, reply_pending=True)
    state.hold_until = time.monotonic() + 0.15
    oai = _FakeOai()
    adapter._schedule_reply(oai, state)  # type: ignore[arg-type]
    state.caller_speaking = True  # they carried on talking
    await asyncio.sleep(0.4)
    assert oai.sent == []


async def test_hold_is_released_as_soon_as_the_text_looks_finished() -> None:
    state = _state(response_active=False, caller_speaking=False, reply_pending=True)
    state.utterance_text = " haan, mera matlab"
    state.hold_until = time.monotonic() + 5  # long hold
    oai, call = _FakeOai(), _FakeCall()
    adapter._schedule_reply(oai, state)  # type: ignore[arg-type]  # starts the hold task
    await _feed(state, oai, call, " price", " kya", " hai", "?")
    await asyncio.sleep(0.1)
    assert state.hold_until == 0.0
    assert "response.create" in oai.types()


async def test_hold_persists_while_text_still_looks_unfinished() -> None:
    state = _state(response_active=False, caller_speaking=False, reply_pending=True)
    state.utterance_text = " haan"
    state.hold_until = time.monotonic() + 5
    oai, call = _FakeOai(), _FakeCall()
    await _feed(state, oai, call, ",", " mera", " matlab")
    await asyncio.sleep(0.05)
    assert state.hold_until > time.monotonic()
    assert oai.sent == []


async def test_warm_up_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("network down")

    monkeypatch.setattr(turn_controller, "judge_turn", boom)
    await turn_controller.warm_up("key")  # must swallow the failure
