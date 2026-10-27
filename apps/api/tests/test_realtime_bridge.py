"""Realtime bridge session config and the native barge-in flag (VOICE_NATIVE_BARGE_IN).

No network: the OpenAI and phone sockets are fakes (see test_voice_interruption.py). These prove what
we send; they cannot prove call latency or tone, which need live calls.
"""

from __future__ import annotations

import asyncio

import pytest

from apps.api.channels.voice import realtime_bridge
from apps.api.channels.voice.greeting import greeting_instruction
from apps.api.config import Settings, settings
from apps.api.tests.test_voice_interruption import Call, _interrupt, base  # noqa: F401 - base is an autouse fixture


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
    await call.event("input_audio_buffer.speech_started")  # no wait: no duration / transcript judgement
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
    await _interrupt(call, wait=0.0)
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")
    await call.event("conversation.item.input_audio_transcription.completed", transcript="hmm thik hai")
    assert call.state.reply_pending is False
