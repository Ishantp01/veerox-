"""Tests for apps.api.channels.voice.elevenlabs_client — the streaming-TTS
session's message framing, independent of a real ElevenLabs connection.
"""

from __future__ import annotations

import json

import pytest

from apps.api.channels.voice import elevenlabs_client


class _FakeElevenLabsWS:
    """Minimal async-iterable stand-in for a websockets connection.

    Records everything sent, and yields the pre-seeded server messages back
    to the reader task via async iteration, like a real connection would.
    """

    def __init__(self, server_messages: list[dict[str, object]]) -> None:
        self.sent: list[dict[str, object]] = []
        self._server_messages = server_messages
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> "_FakeElevenLabsWS":
        self._iter = iter(self._server_messages)
        return self

    async def __anext__(self) -> str:
        try:
            return json.dumps(next(self._iter))
        except StopIteration:
            raise StopAsyncIteration


def test_is_configured_reflects_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", None)
    assert elevenlabs_client.is_configured() is False

    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    assert elevenlabs_client.is_configured() is True


async def test_session_sends_handshake_then_text_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ws = _FakeElevenLabsWS(server_messages=[{"audio": "YWJj", "isFinal": True}])

    async def fake_connect(url: str) -> _FakeElevenLabsWS:
        assert "some-test-voice-id" in url
        assert "output_format=ulaw_8000" in url
        return fake_ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_voice_id", "some-test-voice-id")

    async with elevenlabs_client.ElevenLabsTTSSession() as session:
        await session.send_text("Hello ")
        await session.send_text("world")
        await session.finish()

        chunks = [chunk async for chunk in session.audio_chunks()]

    assert chunks == ["YWJj"]
    # First message is the handshake (carries the API key), then the two
    # text chunks, then the empty-text flush.
    assert fake_ws.sent[0]["xi_api_key"] == "sk_test"
    assert fake_ws.sent[1] == {"text": "Hello "}
    assert fake_ws.sent[2] == {"text": "world"}
    assert fake_ws.sent[3] == {"text": ""}
    assert fake_ws.closed is True


async def test_audio_chunks_stops_on_is_final(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ws = _FakeElevenLabsWS(
        server_messages=[
            {"audio": "aa==", "isFinal": False},
            {"audio": "bb==", "isFinal": True},
        ]
    )

    async def fake_connect(url: str) -> _FakeElevenLabsWS:
        return fake_ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")

    async with elevenlabs_client.ElevenLabsTTSSession() as session:
        await session.finish()
        chunks = [chunk async for chunk in session.audio_chunks()]

    assert chunks == ["aa==", "bb=="]


async def test_server_error_message_ends_stream_without_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the bug that shipped: ElevenLabs reports request
    failures (bad voice_id, quota, auth, ...) as a JSON error message before
    closing the socket, not as a raised exception. Swallowing it silently
    made a real call go dead with no audio and no clue why — this must
    surface as a log line and still end the audio stream cleanly."""
    fake_ws = _FakeElevenLabsWS(
        server_messages=[
            {"message": "A voice with voice_id bad-id does not exist.", "error": "voice_id_does_not_exist"}
        ]
    )

    async def fake_connect(url: str) -> _FakeElevenLabsWS:
        return fake_ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")

    async with elevenlabs_client.ElevenLabsTTSSession() as session:
        await session.finish()
        chunks = [chunk async for chunk in session.audio_chunks()]

    assert chunks == []
