"""LocalVAD (energy gate) and its use in native barge-in."""

from __future__ import annotations

import asyncio
import base64

import numpy as np
import pytest

from apps.api.channels.voice import adapter, local_vad
from apps.api.config import settings
from apps.api.tests.test_voice_interruption import Call, base  # noqa: F401 - base is an autouse fixture


def _ulaw(samples: np.ndarray) -> str:
    # encode int16 -> mu-law via nearest lookup in the decode table
    table = local_vad._ULAW
    idx = np.abs(samples[:, None].astype(np.float32) - table[None, :]).argmin(axis=1).astype(np.uint8)
    return base64.b64encode(idx.tobytes()).decode()


def _tone(amp: float, seconds: float) -> str:
    t = np.arange(int(8000 * seconds)) / 8000
    return _ulaw((amp * np.sin(2 * np.pi * 220 * t)).astype(np.int16))


def test_silence_is_not_speech() -> None:
    v = local_vad.LocalVAD()
    v.feed(_ulaw(np.zeros(1600, dtype=np.int16)))
    assert v.run_frames == 0 and not v.sustained(0.1)


def test_loud_continuous_voice_is_sustained_after_350ms() -> None:
    v = local_vad.LocalVAD()
    v.feed(_tone(30, 0.5))  # quiet line first: sets the noise floor
    v.feed(_tone(6000, 0.2))
    assert not v.sustained(0.35)
    v.feed(_tone(6000, 0.3))
    assert v.sustained(0.35)


def test_a_dropout_resets_the_run() -> None:
    v = local_vad.LocalVAD()
    v.feed(_tone(30, 0.3))
    v.feed(_tone(6000, 0.3))
    v.feed(_ulaw(np.zeros(320, dtype=np.int16)))
    assert v.run_frames == 0


def test_bad_payload_never_raises() -> None:
    local_vad.LocalVAD().feed("not base64 !!")


async def test_native_barge_in_waits_for_the_energy_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_safe_cancel", True)
    monkeypatch.setattr(settings, "voice_native_barge_in", True)
    monkeypatch.setattr(adapter, "NATIVE_BARGE_IN_MIN_SPEECH_SECONDS", 0.05)
    call = Call()
    call.state.vad = local_vad.LocalVAD()
    await call.agent_starts_answering()
    await call.event("input_audio_buffer.speech_started")
    call.state.utterance_text = "wait stop"
    await asyncio.sleep(0.2)
    assert not call.cleared()  # a command word, but no confident speech yet
    call.state.vad.feed(_tone(30, 0.3))
    call.state.vad.feed(_tone(6000, 0.2))
    await asyncio.sleep(0.2)
    assert call.cleared()
