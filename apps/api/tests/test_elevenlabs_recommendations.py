# ruff: noqa: E501
"""Local checks for the ElevenLabs support recommendations: endpointing thresholds, filler/backchannel handling that
must not change with them, and the audio contract handed to the phone. No network, no calls, no paid audio."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.api.channels.voice import adapter, realtime_bridge, turn_controller
from apps.api.config import Settings, settings

# --- VAD silence threshold ---------------------------------------------------------------------------------------


def test_the_default_silence_threshold_is_unchanged() -> None:
    assert Settings.model_fields["voice_vad_silence_ms"].default == 1100
    assert Settings.model_fields["voice_tts_chunk_schedule"].default == ""
    assert Settings.model_fields["voice_elevenlabs_prewarm"].default is False
    assert Settings.model_fields["voice_local_vad"].default is False
    assert Settings.model_fields["voice_smart_turn_taking"].default is False


@pytest.mark.parametrize("ms", [650, 800, 900, 1100])
def test_the_configured_silence_threshold_reaches_the_realtime_session(monkeypatch: pytest.MonkeyPatch, ms: int) -> None:
    monkeypatch.setattr(settings, "voice_vad_silence_ms", ms)
    monkeypatch.setattr(settings, "voice_adaptive_endpointing", False)
    vad = realtime_bridge._session_update_event("PROMPT")["session"]["audio"]["input"]["turn_detection"]
    assert vad["silence_duration_ms"] == ms
    # the app, not the provider, decides when to answer and when to cut the agent off
    assert vad["create_response"] is False and vad["interrupt_response"] is False


# --- fillers, acknowledgements, real answers and interruptions (pinned before any endpointing change) ---------------


@pytest.mark.parametrize("text", ["uh", "um", "hmm", "hmm hmm"])
def test_fillers_never_count_as_an_answer_or_interruption(text: str) -> None:
    assert turn_controller.classify_partial(text) == "filler"
    assert turn_controller.classify_partial(text, True) == "filler"


@pytest.mark.parametrize("text", ["okay", "ok", "yes", "right", "haan", "ji haan"])
def test_acknowledgements_are_backchannel_and_answered_without_a_wait(text: str) -> None:
    assert turn_controller.is_extended_backchannel(text)
    assert turn_controller.classify_partial(text) == "filler"
    assert turn_controller.hold_decision(text, True) == (0.0, None)


@pytest.mark.parametrize("text", ["okay but what is the price", "mujhe price batao", "rukiye ek second", "stop"])
def test_meaningful_speech_is_a_real_turn(text: str) -> None:
    assert not turn_controller.is_extended_backchannel(text)
    assert turn_controller.classify_partial(text) == "real"


def test_genuine_interruptions_still_cut_the_agent() -> None:
    assert turn_controller.classify_partial("actually no wait") == "real"
    assert turn_controller.classify_partial("actually no wait", True) == "real"


# --- the audio contract with the phone ---------------------------------------------------------------------------


def test_plivo_frames_are_8khz_mulaw_matching_the_stream_element() -> None:
    frame = adapter._media_message("plivo", None, "QUJD")
    assert frame["media"] == {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": "QUJD"}
    assert adapter._media_message("twilio", "MZ1", "QUJD") == {
        "event": "media", "streamSid": "MZ1", "media": {"payload": "QUJD"}
    }
    # the leg we asked Plivo to open carries the same codec, so what we send back is never reinterpreted
    from apps.api.channels.voice import webhook

    assert 'contentType="audio/x-mulaw;rate=8000"' in Path(webhook.__file__).read_text(encoding="utf-8")


def test_the_only_output_format_requested_from_elevenlabs_is_the_phone_format() -> None:
    import inspect

    from apps.api.channels.voice import elevenlabs_client

    source = inspect.getsource(elevenlabs_client.ElevenLabsTTSSession.__aenter__)
    assert "output_format=ulaw_8000" in source and "pcm_" not in source and "mp3_" not in source
