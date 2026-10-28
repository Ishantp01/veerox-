"""Tests for channels/voice/latency.py — the per-turn stage clock, percentile
maths and the log-report helpers. Pure functions and a fake clock: nothing here
touches the network, a database or a real call.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from apps.api.channels.voice import latency
from apps.api.channels.voice.latency import (
    STATS,
    LatencyTracker,
    compute_segments,
    percentile,
    summarize,
    summarize_events,
)


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000.0


class _Recorder:
    """Stand-in for the module's structlog logger."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    warning = info

    def named(self, event: str) -> list[dict[str, Any]]:
        return [kw for name, kw in self.events if name == event]


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(latency, "logger", recorder)
    STATS.reset()
    yield recorder
    STATS.reset()


# --- percentile maths ------------------------------------------------------------------------------


def test_percentile_nearest_rank() -> None:
    data = list(range(1, 101))  # 1..100
    assert percentile(data, 50) == 50
    assert percentile(data, 95) == 95
    assert percentile(data, 99) == 99
    assert percentile([7], 99) == 7
    assert percentile([], 50) is None


def test_summarize_reports_sample_size() -> None:
    out = summarize([100, 200, 300])
    assert out["n"] == 3 and out["p50"] == 200
    assert summarize([])["p50"] is None and summarize([])["n"] == 0


# --- segment maths -----------------------------------------------------------------------------------


def test_segments_for_the_elevenlabs_path() -> None:
    marks = {
        "speech_stopped": 0.0,
        "committed": 0.1,
        "response_requested": 0.4,
        "openai_first_output": 0.9,
        "tts_connect_started": 0.9,
        "tts_ready": 1.2,
        "tts_first_chunk": 1.5,
        "first_audio_sent": 1.52,
    }
    seg = compute_segments(marks)
    assert seg["vad_to_commit"] == 100
    assert seg["commit_to_request"] == 300
    assert seg["request_to_model_output"] == 500
    assert seg["tts_connect_wait"] == 300  # socket opened only when text arrived
    assert seg["tts_first_chunk"] == 300  # from ready to first audio
    assert seg["chunk_to_provider"] == 20
    assert seg["request_to_first_audio"] == 1120
    assert seg["total"] == 1520


def test_a_prewarmed_socket_costs_no_connect_wait() -> None:
    marks = {
        "speech_stopped": 0.0,
        "response_requested": 0.3,
        "tts_connect_started": 0.35,
        "tts_ready": 0.55,  # ready before the model's first token
        "openai_first_output": 0.8,
        "tts_first_chunk": 1.0,
        "first_audio_sent": 1.0,
    }
    seg = compute_segments(marks)
    assert seg["tts_connect_wait"] == 0
    assert seg["tts_first_chunk"] == 200  # measured from the first token, not from ready


def test_segments_for_the_native_openai_voice_path() -> None:
    seg = compute_segments(
        {"speech_stopped": 0.0, "response_requested": 0.2, "openai_first_output": 0.6, "first_audio_sent": 0.61}
    )
    assert seg["model_output_to_audio_sent"] == 10
    assert "tts_connect_wait" not in seg and "chunk_to_provider" not in seg


def test_a_turn_without_speech_stopped_totals_from_the_request() -> None:
    """The greeting has no caller speech to measure from."""
    seg = compute_segments({"response_requested": 5.0, "first_audio_sent": 6.2})
    assert seg["total"] == 1200 == seg["request_to_first_audio"]


# --- tracker ------------------------------------------------------------------------------------------


def test_tracker_emits_one_line_per_turn_with_ids_and_no_content(rec: _Recorder) -> None:
    clock = _Clock()
    t = LatencyTracker(org_id="org-1", call_uuid="call-1", provider="plivo", enabled=True, clock=clock)
    t.begin_turn()
    t.mark("speech_stopped")
    clock.advance(100)
    t.mark("committed")
    t.note(agent_was_busy=False)
    clock.advance(300)
    t.mark("response_requested")
    clock.advance(600)
    t.mark("openai_first_output")
    clock.advance(200)
    t.mark_first_audio_sent()
    t.mark_first_audio_sent()  # later frames of the same turn must not re-emit

    (line,) = rec.named("voice_turn_latency")
    assert line["org_id"] == "org-1" and line["call_uuid"] == "call-1" and line["provider"] == "plivo"
    assert line["total_ms"] == 1200
    assert line["agent_was_busy"] is False
    assert line["marks_ms"]["first_audio_sent"] == 1200
    # Identifiers and millisecond offsets only: nothing a caller said or their number.
    assert "caller" not in line and "text" not in line
    assert STATS.snapshot()["total"]["n"] == 1


def test_marks_after_emission_and_without_a_turn_are_ignored(rec: _Recorder) -> None:
    clock = _Clock()
    t = LatencyTracker(enabled=True, clock=clock)
    t.mark("committed")  # no open turn
    t.mark_first_audio_sent()
    assert rec.events == []
    t.begin_turn()
    t.mark("response_requested")
    t.mark_first_audio_sent()
    t.mark("tts_first_chunk")  # after emission
    assert len(rec.named("voice_turn_latency")) == 1


def test_first_mark_wins_so_a_tool_followup_does_not_reset_the_clock(rec: _Recorder) -> None:
    clock = _Clock()
    t = LatencyTracker(enabled=True, clock=clock)
    t.begin_turn()
    t.mark("response_requested")
    clock.advance(1500)  # the model called a tool
    t.note_tool_call()
    t.mark("response_requested")  # the follow-up response.create
    clock.advance(500)
    t.mark_first_audio_sent()
    (line,) = rec.named("voice_turn_latency")
    assert line["total_ms"] == 2000 and line["tool_calls"] == 1


def test_a_new_turn_discards_one_that_never_spoke(rec: _Recorder) -> None:
    t = LatencyTracker(enabled=True, clock=_Clock())
    t.begin_turn()
    t.mark("committed")  # a filler that was dropped: no audio ever
    t.begin_turn()
    t.mark("response_requested")
    t.mark_first_audio_sent()
    (line,) = rec.named("voice_turn_latency")
    assert line["turn"] == 2


def test_disabled_tracker_is_inert(rec: _Recorder) -> None:
    t = LatencyTracker(enabled=False, clock=_Clock())
    t.begin_turn()
    t.mark("response_requested")
    t.mark_first_audio_sent()
    t.interruption_detected()
    assert rec.events == [] and STATS.snapshot() == {}


def test_playback_estimate_is_logged_once_and_labelled_an_estimate(rec: _Recorder) -> None:
    clock = _Clock()
    t = LatencyTracker(enabled=True, clock=clock)
    t.begin_turn()
    t.mark("response_requested")
    t.mark_first_audio_sent()
    clock.advance(400)
    t.response_done(playback_remaining_s=2.0)
    t.response_done(playback_remaining_s=2.0)
    (line,) = rec.named("voice_turn_playback")
    assert line["est_playback_ms"] == 2400
    assert "estimated" in line["note"]


def test_interruption_episode_is_timed_end_to_end(rec: _Recorder) -> None:
    clock = _Clock()
    t = LatencyTracker(enabled=True, clock=clock)
    t.interruption_cleared()  # cleared with nothing detected: ignored
    t.interruption_detected()
    clock.advance(150)
    t.interruption_cut_decided()
    clock.advance(40)
    t.interruption_cleared()
    clock.advance(700)
    t.interruption_next_response()
    (line,) = rec.named("voice_interruption_latency")
    assert line["detected_to_cut_decision_ms"] == 150
    assert line["detected_to_cleared_ms"] == 190
    assert line["cleared_to_next_response_ms"] == 700
    assert line["detected_to_next_response_ms"] == 890
    t.interruption_next_response()  # episode closed: no second line
    assert len(rec.named("voice_interruption_latency")) == 1


def test_an_instrumentation_failure_never_reaches_the_call(rec: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    t = LatencyTracker(enabled=True, clock=_Clock())
    t.begin_turn()
    t.mark("response_requested")

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("log sink down")

    monkeypatch.setattr(t, "_emit_turn", boom)
    t.mark_first_audio_sent()  # must not raise


# --- log aggregation / report script ------------------------------------------------------------------


def _load_report_script() -> Any:
    path = Path(__file__).resolve().parents[3] / "scripts" / "voice_latency_report.py"
    spec = importlib.util.spec_from_file_location("voice_latency_report", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_report_script_summarises_json_log_lines() -> None:
    report = _load_report_script()
    lines = [
        "noise that is not json",
        json.dumps({"event": "voice_turn_latency", "segments_ms": {"total": 1000, "tts_first_chunk": 200}}),
        json.dumps({"event": "voice_turn_latency", "segments_ms": {"total": 3000, "tts_first_chunk": 400}}),
        "\x1b[2m2026\x1b[0m " + json.dumps({"event": "voice_turn_latency", "segments_ms": {"total": 2000}}),
        json.dumps({"event": "voice_interruption_latency", "detected_to_cleared_ms": 120}),
        json.dumps({"event": "voice_turn_playback", "est_playback_ms": 4000}),
        json.dumps({"event": "something_else", "segments_ms": {"total": 99999}}),
    ]
    summary = summarize_events(report.iter_events(lines))
    assert summary["total"]["n"] == 3 and summary["total"]["p50"] == 2000
    assert summary["seg.tts_first_chunk"]["n"] == 2
    assert summary["detected_to_cleared_ms"]["p50"] == 120
    assert summary["est_playback_ms"]["p50"] == 4000
    text = report.format_report(summary)
    assert "total" in text and "p95" in text
    assert report.format_report({}) == "No voice latency events found."


def test_summaries_include_the_per_turn_notes_and_endpoint_categories() -> None:
    def turn(total: int, **extra: Any) -> dict[str, Any]:
        return {"event": "voice_turn_latency", "segments_ms": {"total": total}, **extra}

    events = [
        turn(900, endpoint_category="complete", vad_silence_ms=700, tts_phrase_buffer_ms=120,
             tts_send_to_audio_ms=300),
        turn(1700, endpoint_category="unfinished", vad_silence_ms=700, endpoint_hold_ms=1400),
        turn(800),  # a baseline turn carries none of these
    ]
    summary = latency.summarize_events(events)
    assert summary["note.vad_silence_ms"]["n"] == 2 and summary["note.endpoint_hold_ms"]["n"] == 1
    assert summary["note.tts_phrase_buffer_ms"]["p50"] == 120
    assert latency.endpoint_category_counts(events) == {"complete": 1, "unfinished": 1}
