"""Realtime bridge session config and the native barge-in flag (VOICE_NATIVE_BARGE_IN).

No network: the OpenAI and phone sockets are fakes (see test_voice_interruption.py). These prove what
we send; they cannot prove call latency or tone, which need live calls.
"""

from __future__ import annotations

import asyncio

import pytest

from apps.api.channels.voice import adapter, realtime_bridge
from apps.api.channels.voice.greeting import greeting_instruction
from apps.api.config import Settings, settings
from apps.api.tests.test_voice_interruption import Call, _interrupt, base  # noqa: F401 - base is an autouse fixture


@pytest.fixture(autouse=True)
def _short_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter, "NATIVE_BARGE_IN_MIN_SPEECH_SECONDS", 0.05)


def test_session_update_is_native_mulaw_with_server_vad(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_tts_provider", "openai")
    session = realtime_bridge._session_update_event("PROMPT")["session"]
    assert session["instructions"] == "PROMPT"
    assert session["audio"]["input"]["format"] == {"type": "audio/pcmu"}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcmu"}
    assert session["audio"]["output"]["voice"] == settings.openai_realtime_voice
    vad = session["audio"]["input"]["turn_detection"]
    assert vad["type"] == "server_vad"
    assert vad["silence_duration_ms"] == settings.voice_vad_silence_ms
    assert "output_modalities" not in session  # audio comes from the model, not an external TTS


def test_defaults() -> None:
    assert Settings.model_fields["openai_realtime_model"].default == "gpt-4o-realtime-preview"
    assert Settings.model_fields["openai_realtime_voice"].default == "coral"
    assert Settings.model_fields["voice_native_barge_in"].default is False


def test_greeting_is_a_response_create_instruction() -> None:
    text = greeting_instruction(None, "Acme", True)
    assert "AI assistant" in text


async def test_native_barge_in_cuts_immediately_on_speech_started(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_safe_cancel", True)
    monkeypatch.setattr(settings, "voice_native_barge_in", True)
    call = Call()
    await call.agent_starts_answering()
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(0.15)
    assert call.cleared()
    assert len(call.cancels()) == 1


async def test_native_barge_in_ignores_a_short_burst(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_safe_cancel", True)
    monkeypatch.setattr(settings, "voice_native_barge_in", True)
    call = Call()
    await call.agent_starts_answering()
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")  # over before the 0.4 s window
    await asyncio.sleep(0.15)
    assert not call.cleared()
    assert not call.cancels()


@pytest.mark.parametrize("said", ["sahi hai", "theek", "got it", "hmm", "haan ji"])
async def test_native_barge_in_lets_acknowledgments_finish(monkeypatch: pytest.MonkeyPatch, said: str) -> None:
    monkeypatch.setattr(settings, "voice_safe_cancel", True)
    monkeypatch.setattr(settings, "voice_native_barge_in", True)
    call = Call()
    await call.agent_starts_answering()
    await call.event("input_audio_buffer.speech_started")
    call.state.utterance_text = said
    await asyncio.sleep(0.25)
    await call.event("input_audio_buffer.speech_stopped")
    await asyncio.sleep(0.3)
    assert not call.cleared()
    assert not call.cancels()


async def test_native_barge_in_stops_for_a_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_safe_cancel", True)
    monkeypatch.setattr(settings, "voice_native_barge_in", True)
    call = Call()
    await call.agent_starts_answering()
    await call.event("input_audio_buffer.speech_started")
    call.state.utterance_text = "wait stop karo"
    await asyncio.sleep(0.15)
    assert call.cleared()
    assert len(call.cancels()) == 1


async def test_without_flag_speech_started_does_not_cut_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_safe_cancel", True)
    monkeypatch.setattr(settings, "voice_native_barge_in", False)
    call = Call()
    await call.agent_starts_answering()
    await call.event("input_audio_buffer.speech_started")
    assert not call.cleared()
    assert not call.cancels()
    await asyncio.sleep(0.01)


async def test_greeting_is_not_cut_by_an_immediate_hello(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    monkeypatch.setattr(settings, "voice_safe_cancel", True)
    monkeypatch.setattr(settings, "voice_native_barge_in", True)
    call = Call(greeting_guard_until=time.monotonic() + 10.0)
    await call.agent_starts_answering()
    await call.event("input_audio_buffer.speech_started")
    assert not call.cleared()
    assert not call.cancels()


async def test_backchannel_after_native_cut_triggers_no_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_safe_cancel", True)
    monkeypatch.setattr(settings, "voice_native_barge_in", True)
    call = Call()
    await call.agent_starts_answering()
    await _interrupt(call, wait=0.15)
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")
    await call.event("conversation.item.input_audio_transcription.completed", transcript="hmm thik hai")
    assert call.state.reply_pending is False


# --- VOICE_GREETING_BARGE_IN: a real question stops the opening greeting, "hello?" does not ---


@pytest.fixture
def _greeting_barge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_safe_cancel", True)
    monkeypatch.setattr(settings, "voice_greeting_barge_in", True)
    monkeypatch.setattr(adapter, "GREETING_BARGE_IN_MIN_SPEECH_SECONDS", 0.05)
    monkeypatch.setattr(adapter, "GREETING_BARGE_IN_NO_TEXT_SECONDS", 0.3)


async def _greeting_call() -> Call:
    import time

    call = Call(greeting_guard_until=time.monotonic() + 10.0)
    await call.agent_starts_answering()
    return call


async def test_greeting_barge_in_off_keeps_the_greeting_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_greeting_barge_in", False)
    call = await _greeting_call()
    await call.event("input_audio_buffer.speech_started")
    call.state.utterance_text = "mujhe pricing ke baare mein janna hai"
    await asyncio.sleep(0.2)
    assert not call.cleared()


async def test_a_question_stops_the_greeting_and_owes_the_ai_disclosure(_greeting_barge: None) -> None:
    call = await _greeting_call()
    await call.event("input_audio_buffer.speech_started")
    call.state.utterance_text = "mujhe pricing ke baare mein janna hai"
    await asyncio.sleep(0.25)
    assert call.cleared()
    assert len(call.cancels()) == 1
    assert call.state.greeting_guard_until == 0.0
    assert call.state.disclosure_owed is True


@pytest.mark.parametrize("said", ["hello", "hello hello", "haan ji", "hmm"])
async def test_a_reflexive_hello_does_not_stop_the_greeting(_greeting_barge: None, said: str) -> None:
    call = await _greeting_call()
    await call.event("input_audio_buffer.speech_started")
    call.state.utterance_text = said
    await asyncio.sleep(0.15)
    await call.event("input_audio_buffer.speech_stopped")
    await asyncio.sleep(0.3)
    assert not call.cleared()
    assert not call.state.disclosure_owed


async def test_a_short_burst_during_the_greeting_is_ignored(_greeting_barge: None) -> None:
    call = await _greeting_call()
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await asyncio.sleep(0.2)
    assert not call.cleared()


async def test_long_speech_without_a_transcript_stops_the_greeting(_greeting_barge: None) -> None:
    call = await _greeting_call()
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(0.6)
    assert call.cleared()


async def test_the_reply_after_a_cut_greeting_carries_the_disclosure(_greeting_barge: None) -> None:
    call = await _greeting_call()
    await call.event("input_audio_buffer.speech_started")
    call.state.utterance_text = "pricing kya hai"
    await asyncio.sleep(0.25)
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("response.done", response={"id": "resp_1"})
    await call.event("input_audio_buffer.committed")
    await asyncio.sleep(0.2)
    texts = [str(m.get("response", {}).get("instructions", "")) for m in call.replies()]
    assert any("AI assistant" in t for t in texts)
