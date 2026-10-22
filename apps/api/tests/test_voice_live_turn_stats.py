"""Phase 8H: privacy-safe counters for the live turn-detection path.

The path adds a second OpenAI connection, a warm-up request and up to three judging requests per call, and it decides
when the agent is cut off. These counters make that visible after a call: timeouts, extra provider requests and
interruption decisions. They are numbers and fixed labels only, never words.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest

from apps.api.channels.voice import adapter, turn_controller
from apps.api.channels.voice.turn_controller import LiveTurnStats
from apps.api.core.llm import ChatResult

ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER = uuid.UUID("00000000-0000-0000-0000-000000000ccc")
SECRET_WORDS = "mera number 9876543210 hai aur mera naam Rahul Verma hai"


class _Phone:
    async def send_text(self, message: str) -> None:
        return None


class _Agent:
    async def send(self, message: str) -> None:
        return None


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, *a: Any, **k: Any) -> None:
        self.events.append((event, k))

    warning = info


def _state() -> adapter.CallState:
    return adapter.CallState(user_id=USER, conversation_id=uuid.uuid4(), org_id=ORG)


def _chat(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    async def fake(*a: Any, **k: Any) -> ChatResult:
        if isinstance(outcome, BaseException):
            raise outcome
        return ChatResult(content=outcome, tokens_in=40, tokens_out=6)

    monkeypatch.setattr("apps.api.core.llm.chat_completion", fake)


# --- counting the judging requests ----------------------------------------------------------------------------------------------


async def test_a_successful_judgement_counts_one_request_and_its_label(monkeypatch: pytest.MonkeyPatch) -> None:
    _chat(monkeypatch, '{"label": "QUESTION", "confidence": 0.9}')
    state = _state()
    state.utterance_text, state.cut_eval_deadline = SECRET_WORDS, time.monotonic() + 60
    await adapter._live_llm_check(_Agent(), _Phone(), state, _Log())  # type: ignore[arg-type]
    stats = state.live_stats
    assert (stats.judge_requests, stats.judge_timeouts, stats.judge_errors, stats.judge_unparsable) == (1, 0, 0, 0)
    assert stats.judge_labels == {"question": 1} and stats.extra_provider_requests == 1


@pytest.mark.parametrize(
    ("outcome", "field"),
    [
        (TimeoutError(), "judge_timeouts"),
        (RuntimeError("network down"), "judge_errors"),
        ("not json at all", "judge_unparsable"),
    ],
)
async def test_each_way_a_judgement_can_fail_is_counted_separately(monkeypatch: pytest.MonkeyPatch, outcome: Any, field: str) -> None:
    _chat(monkeypatch, outcome)
    state = _state()
    state.utterance_text, state.cut_eval_deadline = "kuch", time.monotonic() + 60
    await adapter._live_llm_check(_Agent(), _Phone(), state, _Log())  # type: ignore[arg-type]
    counts = state.live_stats.snapshot()
    assert counts["judge_requests"] == 1 and counts[field] == 1
    assert sum(counts[f] for f in ("judge_timeouts", "judge_errors", "judge_unparsable")) == 1  # exactly one reason


async def test_a_real_timeout_of_the_call_is_counted_as_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(*a: Any, **k: Any) -> ChatResult:
        await asyncio.sleep(5)
        return ChatResult(content="{}", tokens_in=0, tokens_out=0)

    monkeypatch.setattr("apps.api.core.llm.chat_completion", slow)
    stats = LiveTurnStats()
    assert await turn_controller.judge_turn("kuch", None, timeout=0.05, stats=stats) is None
    assert stats.judge_timeouts == 1 and stats.judge_requests == 1


async def test_an_answer_that_arrives_after_the_window_is_counted_as_late(monkeypatch: pytest.MonkeyPatch) -> None:
    _chat(monkeypatch, '{"label": "QUESTION", "confidence": 0.9}')
    state = _state()
    state.utterance_text, state.cut_eval_deadline = "kuch", time.monotonic() - 1
    await adapter._live_llm_check(_Agent(), _Phone(), state, _Log())  # type: ignore[arg-type]
    assert state.live_stats.judge_late == 1 and state.live_stats.cuts == {}  # it changed nothing


async def test_the_warm_up_is_an_extra_request_but_not_a_judgement(monkeypatch: pytest.MonkeyPatch) -> None:
    _chat(monkeypatch, '{"label": "FILLER", "confidence": 0.9}')
    stats = LiveTurnStats()
    await turn_controller.warm_up(None, stats)
    assert (stats.warmup_requests, stats.judge_requests, stats.judge_labels) == (1, 0, {})
    _chat(monkeypatch, RuntimeError("down"))
    await turn_controller.warm_up(None, stats)
    assert stats.warmup_requests == 2 and stats.warmup_failures == 1 and stats.judge_errors == 0


def test_the_transcription_connection_counts_as_an_extra_provider_connection_only_when_it_started() -> None:
    stats = LiveTurnStats()
    assert stats.extra_provider_requests == 0
    stats.transcriber = "failed"
    assert stats.extra_provider_requests == 0
    stats.transcriber, stats.warmup_requests, stats.judge_requests = "started", 1, 2
    assert stats.extra_provider_requests == 4


# --- counting the interruption decisions --------------------------------------------------------------------------------------------


class _Spy(_Agent):
    pass


async def test_a_cut_is_counted_by_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    state.response_active = True
    state.suppress_response_audio = False
    for reason in ("live_real", "live_fallback", "live_fallback", "duration"):
        state.cut_eval_deadline = time.monotonic() + 5
        state.response_active = True
        state.ack_stage = None
        await adapter._cut_now(_Agent(), _Phone(), state, _Log(), reason)  # type: ignore[arg-type]
    assert state.live_stats.cuts == {"live_real": 1, "live_fallback": 2, "duration": 1}
    assert state.live_stats.snapshot()["cuts_total"] == 4


async def test_a_filler_the_agent_talked_over_is_counted_not_cut() -> None:
    state = _state()
    state.caller_speaking = False
    adapter._drop_filler_reply(state)
    state.caller_speaking = True
    adapter._drop_filler_reply(state)  # still speaking: nothing was dropped yet
    assert state.live_stats.filler_ignored == 1 and state.live_stats.cuts == {}


def test_only_fixed_reasons_and_labels_can_ever_become_a_counter_key() -> None:
    stats = LiveTurnStats()
    stats.count_cut("live real; DROP TABLE users -- 9876543210")
    stats.count_cut("live_llm_question")
    stats.count_label("not a label 9876543210")
    stats.count_label("OBJECTION")
    for i in range(100):
        stats.count_cut(f"reason_{i}")
    assert set(stats.cuts) <= turn_controller.CUT_REASONS | {"other"} and set(stats.judge_labels) <= turn_controller.JUDGE_LABELS | {"other"}
    assert stats.cuts["other"] == 101 and stats.cuts["live_llm_question"] == 1 and stats.judge_labels == {"other": 1, "objection": 1}


def test_every_cut_reason_the_adapter_can_pass_is_in_the_fixed_set() -> None:
    import inspect
    import re

    source = inspect.getsource(adapter)
    literal = set(re.findall(r'_(?:cut_now|on_real_turn)\([^)]*?, "([a-z_]+)"\)', source))
    assert literal and literal <= turn_controller.CUT_REASONS, literal - turn_controller.CUT_REASONS
    assert all(f"live_llm_{label.lower()}" in turn_controller.CUT_REASONS for label in turn_controller.TURN_LABELS)


# --- privacy ------------------------------------------------------------------------------------------------------------------------------


async def test_the_summary_holds_numbers_and_fixed_labels_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _chat(monkeypatch, '{"label": "OBJECTION", "confidence": 0.9}')
    state = _state()
    state.utterance_text, state.cut_eval_deadline = SECRET_WORDS, time.monotonic() + 60
    state.caller_speaking = True
    await adapter._live_llm_check(_Agent(), _Phone(), state, _Log())  # type: ignore[arg-type]
    blob = json.dumps(state.live_stats.snapshot())
    for secret in ("9876543210", "Rahul", "Verma", "naam", "mera"):
        assert secret not in blob
    assert all(isinstance(v, (int, str, dict)) for v in state.live_stats.snapshot().values())


def test_the_judgement_log_line_never_carries_the_words_when_transcripts_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.config import settings
    from apps.api.core import privacy

    monkeypatch.setattr(settings, "log_privacy_enabled", True)
    monkeypatch.setattr(settings, "log_transcripts", False)
    event = {"event": "voice_live_llm_verdict", "text": SECRET_WORDS, "label": "QUESTION", "confidence": 0.9}
    blob = json.dumps(privacy.privacy_processor(None, "info", dict(event)), default=str)
    assert "9876543210" not in blob and "Rahul" not in blob and "QUESTION" in blob  # the label stays, the words go


# --- what the end-of-call line can contain ------------------------------------------------------------------------------------------------


def _hostile_stats() -> LiveTurnStats:
    stats = LiveTurnStats()
    stats.transcriber = "started"
    stats.judge_requests, stats.judge_timeouts, stats.judge_errors, stats.judge_late = 3, 1, 1, 1
    stats.count_label("QUESTION")
    stats.count_cut("live_real")
    stats.count_cut("sk-proj-ABCDEF123456 call me on 9876543210")  # a hostile reason string
    stats.count_label("Rahul Verma password hunter2")
    return stats


def test_the_summary_line_has_a_closed_set_of_keys_and_only_counts_and_fixed_labels() -> None:
    fields = turn_controller.summary_fields(_hostile_stats(), 120, 9)
    assert set(fields) <= turn_controller.SUMMARY_KEYS
    required = {"extra_provider_requests", "judge_requests", "judge_timeouts", "judge_errors", "cuts", "cuts_total", "judge_tokens_in", "judge_tokens_out"}
    assert required <= set(fields)  # request counts, timeout/error counts and interruption reasons are all reported
    assert fields["extra_provider_requests"] == 4 and fields["judge_timeouts"] == 1 and fields["judge_errors"] == 1 and fields["cuts_total"] == 2

    def leaves(value: Any) -> list[Any]:
        return [leaf for v in value.values() for leaf in leaves(v)] if isinstance(value, dict) else [value]

    assert all(isinstance(leaf, (int, str)) for leaf in leaves(fields))


def test_nothing_secret_survives_the_summary_even_if_a_hostile_string_reaches_a_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.config import settings
    from apps.api.core import privacy

    monkeypatch.setattr(settings, "log_privacy_enabled", True)
    monkeypatch.setattr(settings, "log_transcripts", False)
    event = {"event": "voice_live_turn_summary", **turn_controller.summary_fields(_hostile_stats(), 120, 9)}
    blob = json.dumps(privacy.privacy_processor(None, "info", event), default=str)
    for leaked in ("9876543210", "sk-proj", "ABCDEF123456", "Rahul", "Verma", "hunter2", " "):
        if leaked == " ":
            assert " " not in blob.replace(", ", "").replace(": ", "")  # keys and labels are single tokens, never sentences
        else:
            assert leaked not in blob


def test_the_bridge_emits_the_summary_only_through_the_closed_field_builder() -> None:
    import inspect

    from apps.api.channels.voice import realtime_bridge

    source = inspect.getsource(realtime_bridge)
    assert "voice_live_turn_summary" in source and "turn_controller.summary_fields(" in source
    assert source.count('"voice_live_turn_summary"') == 1
