"""Phase 8L: where a turn's time goes, and a stale-state bug found in the Phase 8K live call.

Live call: the caller said "haan" (a nod) while the agent was still talking. The log said "backchannel ignored", yet the agent
answered it, after waiting for its own earlier audio. Cause: every ordinary turn's transcript was appended to
``overlap_transcripts`` (a fresh commit always has ``reply_pending`` set), nothing emptied the list in the default
interruption mode, and the backchannel drop only runs when that list is empty.

The timing tests measure OUR scheduling overhead with fake sockets: they prove the code adds no waits of its own (a reply never
waits for the transcript, a nod never triggers an answer), not how fast OpenAI or the network are.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from typing import Any

import pytest
import structlog

from apps.api.channels.voice import adapter
from apps.api.config import settings
from apps.api.tests.test_voice_interruption import Call, base  # noqa: F401  (autouse fixture comes with it)

HELLO = "अलो"
NOD = "हाँ"


async def _caller_turn(call: Call, transcript: str | None = None) -> None:
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")
    if transcript is not None:
        await call.event("conversation.item.input_audio_transcription.completed", transcript=transcript)


async def _settle(call: Call, seconds: float = 0.15) -> None:
    await asyncio.sleep(seconds)


# --- the stale overlap list ------------------------------------------------------------------------------------------------------------


async def test_an_ordinary_turn_leaves_nothing_queued_for_a_read_back() -> None:
    call = Call()
    await _caller_turn(call, HELLO)
    assert call.state.overlap_transcripts == []  # before: ["अलो"], and it stayed there for the rest of the call


async def test_a_nod_while_the_agent_is_talking_is_not_answered_even_after_earlier_turns(base: Any) -> None:
    call = Call()
    await _caller_turn(call, HELLO)
    await _settle(call)
    call.state.reply_pending = False
    call.state.response_active = False
    answered = len(call.replies())
    call.state.playback_end = time.monotonic() + 0.4  # the agent's earlier reply is still audible
    await _caller_turn(call, NOD)
    assert call.state.reply_pending is False  # dropped, as the log line claimed
    await asyncio.sleep(0.7)
    assert len(call.replies()) == answered  # and nothing was answered


async def test_a_real_question_spoken_over_the_agent_is_still_queued_in_finish_first_mode(monkeypatch: pytest.MonkeyPatch, base: Any) -> None:
    monkeypatch.setattr(settings, "voice_interruption_mode", "finish_first")
    call = Call()
    await call.agent_starts_answering()
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")
    assert call.state.restate_next is True
    await call.event("conversation.item.input_audio_transcription.completed", transcript="Iska price kya hai")
    assert call.state.overlap_transcripts == ["Iska price kya hai"]  # still collected for the read-back


async def test_a_plain_reply_consumes_whatever_overlap_was_queued() -> None:
    call = Call()
    call.state.overlap_transcripts = ["stale"]
    call.state.reply_pending = True
    await adapter._fire_pending_reply(call.agent, call.state)  # type: ignore[arg-type]
    assert call.state.overlap_transcripts == [] and len(call.replies()) == 1


# --- no unnecessary waits on the critical path ---------------------------------------------------------------------------------------------


async def test_the_reply_does_not_wait_for_the_transcript() -> None:
    call = Call()
    started = time.monotonic()
    await _caller_turn(call)  # committed; the transcript has NOT arrived (it took 4.7 s on the live call)
    while not call.replies() and time.monotonic() - started < 1.0:
        await asyncio.sleep(0.005)
    sent_after = time.monotonic() - started
    assert len(call.replies()) == 1 and sent_after < 0.1
    await call.event("conversation.item.input_audio_transcription.completed", transcript="Haan boliye, kya hai")
    await _settle(call)
    assert len(call.replies()) == 1  # the late transcript does not trigger a second answer


async def test_a_reply_waits_for_the_agents_own_audio_and_not_longer() -> None:
    call = Call()
    call.state.playback_end = time.monotonic() + 0.4
    started = time.monotonic()
    await _caller_turn(call)
    await asyncio.sleep(0.25)
    assert call.replies() == []  # never talks over itself
    while not call.replies() and time.monotonic() - started < 1.5:
        await asyncio.sleep(0.01)
    waited = time.monotonic() - started
    assert len(call.replies()) == 1 and 0.38 <= waited < 0.6  # fires as soon as the audio has drained


async def test_two_commits_for_one_utterance_produce_one_reply() -> None:
    call = Call()
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")
    await call.event("input_audio_buffer.committed")  # the server VAD split one utterance
    await _settle(call, 0.3)
    assert len(call.replies()) == 1


async def test_scheduling_overhead_over_many_simulated_turns(capsys: pytest.CaptureFixture[str]) -> None:
    """p50 / p95 / worst of OUR delay between 'the caller's turn was committed' and 'the reply was requested', for an idle agent."""
    samples: list[float] = []
    for i in range(40):
        call = Call()
        await call.event("input_audio_buffer.speech_started")
        await call.event("input_audio_buffer.speech_stopped")
        started = time.monotonic()
        await call.event("input_audio_buffer.committed")
        while not call.replies():
            await asyncio.sleep(0.001)
        samples.append((time.monotonic() - started) * 1000)
    samples.sort()
    p50, p95, worst = statistics.median(samples), samples[int(len(samples) * 0.95) - 1], samples[-1]
    with capsys.disabled():
        print(f"\n[8L] scheduling overhead, 40 simulated turns: p50 {p50:.1f} ms, p95 {p95:.1f} ms, worst {worst:.1f} ms")
    assert p95 < 50 and worst < 150


# --- the trace line -------------------------------------------------------------------------------------------------------------------------------


async def test_the_trace_lines_say_where_the_time_went_and_never_what_was_said() -> None:
    call = Call()
    call.state.playback_end = time.monotonic() + 0.2
    with structlog.testing.capture_logs() as logs:
        await _caller_turn(call, "Mera number 9876543210 hai, Rahul Verma bol raha hoon")
        await _settle(call, 0.5)
    trace = [e for e in logs if e["event"] == "voice_turn_trace"]
    kinds = [e["kind"] for e in trace]
    assert "transcript_arrived" in kinds and "reply_waiting" in kinds and "reply_firing" in kinds
    waiting = next(e for e in trace if e["kind"] == "reply_waiting")
    assert waiting["on"] == "agent_audio" and 0 <= waiting["wait_ms"] <= 300
    text = repr(trace)
    assert "9876543210" not in text and "Rahul" not in text and "Verma" not in text
    assert all(isinstance(v, (int, float, bool, str, type(None))) for e in trace for v in e.values())
    assert not any(e["event"] == "voice_turn_event" and e.get("kind") in ("transcript_arrived", "reply_firing", "backchannel") for e in logs)


async def test_a_nod_is_traced_as_dropped_or_kept() -> None:
    call = Call()
    call.state.playback_end = time.monotonic() + 0.3
    with structlog.testing.capture_logs() as logs:
        await _caller_turn(call, NOD)
        await _settle(call, 0.5)
    nod = [e for e in logs if e["event"] == "voice_turn_trace" and e["kind"] == "backchannel"]
    assert len(nod) == 1 and nod[0]["dropped"] is True and nod[0]["ms_since_commit"] is not None
