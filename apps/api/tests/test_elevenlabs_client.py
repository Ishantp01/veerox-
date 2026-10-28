"""Tests for apps.api.channels.voice.elevenlabs_client — the streaming-TTS
session's message framing, independent of a real ElevenLabs connection.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

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

    def __aiter__(self) -> _FakeElevenLabsWS:
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


def test_phrase_buffer_splits_on_whitespace_after_punctuation() -> None:
    buf = elevenlabs_client.PhraseBuffer()
    assert buf.feed("Aapka plan 3.") == ""  # nothing after the dot yet
    assert buf.feed("5 lakh ka hai. Kya") == "Aapka plan 3.5 lakh ka hai. "
    assert buf.feed(" main aage badhun?") == ""  # trailing "?" has no whitespace after it yet
    assert buf.flush() == "Kya main aage badhun?"


def test_phrase_buffer_holds_short_fragments_and_soft_breaks() -> None:
    buf = elevenlabs_client.PhraseBuffer()
    assert buf.feed("Haan. ") == ""  # too short to speak alone
    # A short comma never splits.
    assert buf.feed("Bilkul, main samjha. ") == "Haan. Bilkul, main samjha. "
    # A comma splits once the phrase is long enough to carry its own intonation.
    assert buf.feed("Dekhiye, aapke business ke liye yeh kaafi useful rahega, ") == (
        "Dekhiye, aapke business ke liye yeh kaafi useful rahega, "
    )


async def test_phrase_flush_sends_flush_true_and_final_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ws = _FakeElevenLabsWS(server_messages=[{"audio": "YWJj", "isFinal": True}])

    async def fake_connect(url: str) -> _FakeElevenLabsWS:
        return fake_ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.setattr(elevenlabs_client.settings, "voice_tts_phrase_flush", True)

    async with elevenlabs_client.ElevenLabsTTSSession() as session:
        for piece in ["Namaste, main ", "AI assistant hoon. ", "Kaise ", "help karun?"]:
            await session.send_text(piece)
        await session.finish()
        _ = [c async for c in session.audio_chunks()]

    assert fake_ws.sent[1] == {"text": "Namaste, main AI assistant hoon. ", "flush": True}
    assert fake_ws.sent[2] == {"text": "Kaise help karun? "}
    assert fake_ws.sent[3] == {"text": ""}


def test_phrase_buffer_skips_abbreviations_and_initials() -> None:
    buf = elevenlabs_client.PhraseBuffer()
    # "Rs." and an initial inside a long sentence are not sentence ends.
    assert buf.feed("Aapka monthly plan sirf Rs. 500 ka hai aur A. K. Sharma ji ") == ""
    released = buf.feed("ne approve kiya. Aage")
    assert released.startswith("Aapka monthly plan sirf Rs. 500")
    assert released.endswith("A. K. Sharma ji ne approve kiya. ")
    assert buf.flush() == "Aage"


def test_phrase_buffer_short_reply_is_released_only_at_flush() -> None:
    buf = elevenlabs_client.PhraseBuffer()
    assert buf.feed("Ji haan. ") == ""
    assert buf.flush() == "Ji haan. "
    assert buf.flush() == ""  # nothing is ever emitted twice


def test_phrase_buffer_never_repeats_text() -> None:
    buf = elevenlabs_client.PhraseBuffer()
    text = (
        "Namaste ji, main AI assistant hoon. Aapka naam kya hai? "
        "Main Rs. 5 lakh wala plan bataun. Theek hai?"
    )
    sent = "".join(buf.feed(t) for t in text.split(" ") for t in (t + " ",)) + buf.flush()
    assert sent.split() == text.split()


@pytest.mark.parametrize(
    "text",
    [
        "Namaste, main Veerox ka AI assistant hoon. Kya main aapse do minute baat kar sakta hoon?"
        " Bas ek chhota demo...",
        "जी बिल्कुल, मैं अभी आपकी अपॉइंटमेंट बुक कर देता हूँ। क्या आप कल शाम 5 बजे आ सकते हैं?",
        "Sure, I have booked that for you. You will get a confirmation shortly! Is that okay?",
        "Aapka plan Rs. 4,999 per month se shuru hota hai, aur setup free hai;"
        " koi hidden charge nahi hai: bilkul nahi.",
    ],
)
def test_phrase_flush_keeps_every_character_and_all_punctuation(text: str) -> None:
    """Phrase streaming only chooses WHERE to cut the stream; the words, order and punctuation
    that reach the voice (which shape its intonation) are exactly the model's text."""
    buf = elevenlabs_client.PhraseBuffer()
    pieces = [buf.feed(token) for token in re.findall(r"\s*\S+\s?", text)] + [buf.flush()]
    assert "".join(pieces).split() == text.split()
    # Nothing added, dropped or reordered.
    assert "".join(pieces).replace(" ", "") == text.replace(" ", "")
    cuts = [p for p in pieces if p]
    # No tiny fragment is synthesised on its own.
    assert all(len(p.strip()) >= 12 for p in cuts[:-1])


async def test_first_text_and_first_send_are_timed_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gap is how long phrase buffering held the first words (zero without it)."""
    clock = iter([100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5])
    # Only this module sees the fake clock.
    fake_time = SimpleNamespace(monotonic=lambda: next(clock))
    monkeypatch.setattr(elevenlabs_client, "time", fake_time)
    ws = _FakeElevenLabsWS(server_messages=[{"audio": "YWJj", "isFinal": True}])

    async def fake_connect(url: str) -> _FakeElevenLabsWS:
        return ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.setattr(elevenlabs_client.settings, "voice_tts_phrase_flush", True)
    async with elevenlabs_client.ElevenLabsTTSSession() as session:
        assert session.first_text_at is None and session.first_send_at is None
        await session.send_text("Namaste, main AI ")  # held: no sentence end yet
        assert session.first_text_at is not None and session.first_send_at is None
        await session.send_text("assistant hoon. Kaise ")  # a phrase is complete: it is sent now
        assert session.first_send_at is not None and session.first_send_at > session.first_text_at
        await session.finish()


async def test_a_reply_sent_only_at_the_end_still_records_its_first_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _FakeElevenLabsWS(server_messages=[{"audio": "YWJj", "isFinal": True}])

    async def fake_connect(url: str) -> _FakeElevenLabsWS:
        return ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.setattr(elevenlabs_client.settings, "voice_tts_phrase_flush", True)
    async with elevenlabs_client.ElevenLabsTTSSession() as session:
        await session.send_text("Ji haan.")  # too short to send alone: held until the turn ends
        assert session.first_send_at is None
        await session.finish()
        assert session.first_send_at is not None
