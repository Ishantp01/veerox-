"""SarvamWSTTSSession against a fake socket: ordering, flush-on-sentence, final event, and silent-close failure."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest

from apps.api.channels.voice import sarvam_client


class FakeWS:
    def __init__(self, incoming: list[Any]) -> None:
        self.sent: list[dict[str, Any]] = []
        self._incoming = list(incoming)
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        if not self._incoming:
            raise ConnectionError("closed without final")
        item = self._incoming.pop(0)
        if item == "hang":
            await asyncio.sleep(10)
        return json.dumps(item)

    async def close(self) -> None:
        self.closed = True


def _audio(n: int) -> dict[str, Any]:
    return {"type": "audio", "data": {"audio": base64.b64encode(b"\x7f" * n).decode()}}


FINAL = {"type": "event", "data": {"event_type": "final"}}


async def _session(ws: FakeWS) -> sarvam_client.SarvamWSTTSSession:
    s = sarvam_client.SarvamWSTTSSession()
    s._ws = ws
    return s


async def test_text_is_sent_at_once_and_sentence_end_flushes() -> None:
    ws = FakeWS([])
    s = await _session(ws)
    await s.send_text("Namaste ji")
    assert [m["type"] for m in ws.sent] == ["text"]
    await s.send_text(", kaise hain?")
    assert [m["type"] for m in ws.sent] == ["text", "text", "flush"]
    assert s.chars_sent == len("Namaste ji") + len(", kaise hain?")


async def test_audio_is_chunked_and_final_marks_the_turn_complete() -> None:
    ws = FakeWS([_audio(3200), _audio(100), FINAL])
    s = await _session(ws)
    chunks = [c async for c in s.audio_chunks()]
    assert [len(base64.b64decode(c)) for c in chunks] == [1600, 1600, 100]
    assert s.final_seen and not s.failed


async def test_close_without_final_fails_the_turn_so_it_is_replayed() -> None:
    ws = FakeWS([_audio(800)])
    s = await _session(ws)
    chunks = [c async for c in s.audio_chunks()]
    assert len(chunks) == 1
    assert s.failed and not s.final_seen
    assert s.fail_reason and s.fail_reason.startswith("sarvam_ws_closed")


async def test_quiet_socket_fails_after_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sarvam_client, "_WS_IDLE_TIMEOUT", 0.05)
    s = await _session(FakeWS(["hang"]))
    assert [c async for c in s.audio_chunks()] == []
    assert s.fail_reason == "sarvam_ws_idle"


async def test_error_event_fails_the_turn() -> None:
    s = await _session(FakeWS([{"type": "error", "data": {"message": "x"}}]))
    assert [c async for c in s.audio_chunks()] == []
    assert s.failed


def test_websocket_mode_is_off_by_default() -> None:
    from apps.api.config import Settings

    assert Settings.model_fields["sarvam_streaming_ws"].default is False
