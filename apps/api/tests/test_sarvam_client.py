"""SarvamTTSSession: clause splitting, ordered audio, failure marking. The HTTP call is faked."""

from __future__ import annotations

import base64

import pytest

from apps.api.channels.voice import sarvam_client
from apps.api.channels.voice.sarvam_client import SarvamTTSSession
from apps.api.config import Settings


def test_engine_defaults_to_elevenlabs() -> None:
    assert Settings.model_fields["voice_tts_engine"].default == "elevenlabs"


async def _run(session: SarvamTTSSession, deltas: list[str]) -> list[bytes]:
    async with session:
        for d in deltas:
            await session.send_text(d)
        await session.finish()
        return [base64.b64decode(c) async for c in session.audio_chunks()]


async def test_clauses_are_spoken_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    spoken: list[str] = []

    async def fake(self: SarvamTTSSession, text: str) -> bytes:
        spoken.append(text)
        return text.encode()

    monkeypatch.setattr(SarvamTTSSession, "_synthesize", fake)
    out = await _run(SarvamTTSSession(), ["Namaste, main Veer bol raha ", "hoon. Aap kaise hain? ", "Theek hai"])
    assert spoken == ["Namaste, main Veer bol raha hoon.", "Aap kaise hain?", "Theek hai"]
    assert b"".join(out) == "".join(spoken).encode()


async def test_a_failed_clause_marks_the_turn_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(self: SarvamTTSSession, text: str) -> bytes:
        raise RuntimeError("rate limited")

    monkeypatch.setattr(SarvamTTSSession, "_synthesize", boom)
    session = SarvamTTSSession()
    out = await _run(session, ["Hello ji. Kaise ho?"])
    assert out == []
    assert session.failed and session.fail_reason == "sarvam:RuntimeError"
    assert session.final_seen is False


async def test_empty_turn_finishes_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    session = SarvamTTSSession()
    assert await _run(session, []) == []
    assert session.final_seen and not session.failed


def test_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sarvam_client.settings, "sarvam_api_key", None)
    assert sarvam_client.is_configured() is False
    monkeypatch.setattr(sarvam_client.settings, "sarvam_api_key", "k")
    assert sarvam_client.is_configured() is True
