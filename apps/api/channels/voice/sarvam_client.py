"""Sarvam AI Bulbul TTS — optional engine for the text-to-speech leg of a call (``VOICE_TTS_ENGINE=sarvam``).

Plugs in where ``ElevenLabsTTSSession`` does (same interface: ``send_text`` / ``finish`` / ``audio_chunks``),
so the adapter's barge-in, recovery and metering paths are unchanged. It uses Sarvam's REST endpoint, not their
WebSocket: an earlier WebSocket integration went silent when Sarvam closed the socket without an error. Here every
request either returns audio or raises, and a failed turn is marked ``failed`` so TTS recovery replays it on
ElevenLabs (see adapter ``_open_elevenlabs_session(safe=True)``).

Text is cut into short clauses as it streams in; each clause is synthesized concurrently and its audio is
released strictly in order. Audio comes back as mu-law 8 kHz, the phone format, so nothing is resampled.

UNVERIFIED on live calls. Measured from a laptop: ~1.2 s for a short sentence, so first-audio latency is the
thing to judge.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, AsyncIterator

import httpx
import structlog
import websockets

from apps.api.config import settings

logger = structlog.get_logger(__name__)

_URL = "https://api.sarvam.ai/text-to-speech"
_BOUNDARY_RE = re.compile(r"(?<=[.?!।…])\s+|\n+")
_BOUNDARY_END_RE = re.compile(r"[.?!।…]\s*$")
_COMMA_RE = re.compile(r"(?<=,)\s+")
_MIN_CLAUSE = 28  # a comma only ends a clause once this many characters are waiting
_PLAYBACK_CHUNK = 1600  # bytes of mu-law (200 ms) per audio message sent to the phone
_REQUEST_TIMEOUT = 8.0


def is_configured() -> bool:
    return bool(settings.sarvam_api_key)


class SarvamTTSSession:
    """One assistant turn: push text in, read ordered mu-law chunks out. Not reusable across turns."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._buf = ""
        self._jobs: asyncio.Queue[asyncio.Task[bytes] | None] = asyncio.Queue()
        self._finished = False
        self.connect_ms: int | None = 0
        self.failed = False
        self.fail_reason: str | None = None
        self.final_seen = False
        self.audio_chunks_received = 0
        self.chars_sent = 0
        self.on_close: Callable[[int], None] | None = None
        self._closed_reported = False

    async def __aenter__(self) -> "SarvamTTSSession":
        self._client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        while not self._jobs.empty():
            job = self._jobs.get_nowait()
            if job is not None and not job.done():
                job.cancel()
        if self._client is not None:
            await self._client.aclose()
        if self.on_close is not None and not self._closed_reported:
            self._closed_reported = True
            try:
                self.on_close(self.chars_sent)
            except Exception:  # noqa: BLE001 - accounting must never affect the call
                pass

    async def _synthesize(self, text: str) -> bytes:
        assert self._client is not None
        started = time.monotonic()
        resp = await self._client.post(
            _URL,
            headers={"api-subscription-key": settings.sarvam_api_key or ""},
            json={
                "text": text,
                "target_language_code": settings.sarvam_language_code,
                "speaker": settings.sarvam_speaker,
                "model": settings.sarvam_model,
                "pace": settings.sarvam_pace,
                "speech_sample_rate": 8000,
                "output_audio_codec": "mulaw",
            },
        )
        resp.raise_for_status()
        audios = resp.json().get("audios") or []
        if not audios:
            raise RuntimeError("sarvam_empty_audio")
        logger.info("sarvam_tts_clause", ms=round((time.monotonic() - started) * 1000), chars=len(text))
        return base64.b64decode(audios[0])

    def _enqueue(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.chars_sent += len(text)
        self._jobs.put_nowait(asyncio.create_task(self._synthesize(text)))

    def _take_ready_clauses(self) -> None:
        """Cut complete clauses off the front of the buffer and start synthesizing them."""
        while True:
            m = _BOUNDARY_RE.search(self._buf)
            if m is None:
                c = _COMMA_RE.search(self._buf)
                if c is None or c.start() < _MIN_CLAUSE:
                    return
                m = c
            self._enqueue(self._buf[: m.start()] + self._buf[m.start() : m.end()].strip())
            self._buf = self._buf[m.end():]

    async def send_text(self, text: str) -> None:
        if not text or self._finished:
            return
        self._buf += text
        self._take_ready_clauses()

    async def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._enqueue(self._buf)
        self._buf = ""
        self._jobs.put_nowait(None)

    async def audio_chunks(self) -> AsyncIterator[str]:
        while True:
            job = await self._jobs.get()
            if job is None:
                self.final_seen = not self.failed
                return
            try:
                audio = await job
            except Exception as exc:  # noqa: BLE001 - a failed clause fails the turn so it can be replayed
                self.failed = True
                self.fail_reason = f"sarvam:{type(exc).__name__}"
                logger.warning("sarvam_tts_failed", error_type=type(exc).__name__)
                return
            for i in range(0, len(audio), _PLAYBACK_CHUNK):
                self.audio_chunks_received += 1
                yield base64.b64encode(audio[i : i + _PLAYBACK_CHUNK]).decode()


_WS_URL = "wss://api.sarvam.ai/text-to-speech/ws"
_WS_MIN_BUFFER = 30  # characters Sarvam waits for before it starts synthesizing on its own
_WS_CONNECT_TIMEOUT = 4.0
_WS_IDLE_TIMEOUT = 6.0  # no frame this long while a turn is open: treat the socket as dead


class SarvamWSTTSSession:
    """Streaming variant (``SARVAM_STREAMING_WS=true``): text goes out as the model produces it and audio comes
    back as soon as Sarvam has it, with no client-side clause buffering. Same interface as ``SarvamTTSSession``.

    Sarvam has closed this socket silently before, so a close or a quiet gap without the ``final`` event marks the
    session ``failed``; the adapter then replays the turn on ElevenLabs (TTS recovery).
    """

    def __init__(self) -> None:
        self._ws: Any = None
        self._finished = False
        self._pending_flush = False
        self.connect_ms: int | None = None
        self.failed = False
        self.fail_reason: str | None = None
        self.final_seen = False
        self.audio_chunks_received = 0
        self.chars_sent = 0
        self.on_close: Callable[[int], None] | None = None
        self._closed_reported = False
        self.first_audio_ms: int | None = None
        self._opened = 0.0

    async def __aenter__(self) -> "SarvamWSTTSSession":
        started = time.monotonic()
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    f"{_WS_URL}?model={settings.sarvam_model}&send_completion_event=true",
                    additional_headers={"Api-Subscription-Key": settings.sarvam_api_key or ""},
                    max_size=None,
                ),
                _WS_CONNECT_TIMEOUT,
            )
            await self._ws.send(
                json.dumps(
                    {
                        "type": "config",
                        "data": {
                            "speaker": settings.sarvam_speaker,
                            "target_language_code": settings.sarvam_language_code,
                            "language_code": settings.sarvam_language_code,
                            "pace": settings.sarvam_pace,
                            "min_buffer_size": _WS_MIN_BUFFER,
                            "max_chunk_length": 200,
                            "output_audio_codec": "mulaw",
                            "speech_sample_rate": 8000,
                        },
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001 - recovery replays the turn on ElevenLabs
            self._fail(f"sarvam_ws_connect:{type(exc).__name__}")
        self.connect_ms = round((time.monotonic() - started) * 1000)
        self._opened = time.monotonic()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self.on_close is not None and not self._closed_reported:
            self._closed_reported = True
            try:
                self.on_close(self.chars_sent)
            except Exception:  # noqa: BLE001 - accounting must never affect the call
                pass

    def _fail(self, reason: str) -> None:
        if not self.failed:
            self.failed = True
            self.fail_reason = reason
            logger.warning("sarvam_ws_failed", reason=reason)

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None or self.failed:
            return
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as exc:  # noqa: BLE001
            self._fail(f"sarvam_ws_send:{type(exc).__name__}")

    async def send_text(self, text: str) -> None:
        if not text or self._finished:
            return
        self.chars_sent += len(text)
        await self._send({"type": "text", "data": {"text": text}})
        # A sentence end flushes at once so audio starts without waiting for the character threshold.
        if _BOUNDARY_END_RE.search(text):
            await self._send({"type": "flush"})

    async def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        await self._send({"type": "flush"})

    async def audio_chunks(self) -> AsyncIterator[str]:
        if self._ws is None or self.failed:
            return
        while True:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), _WS_IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                self._fail("sarvam_ws_idle")
                return
            except Exception as exc:  # noqa: BLE001 - closed without a final event
                self._fail(f"sarvam_ws_closed:{type(exc).__name__}")
                return
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue
            kind = msg.get("type")
            data = msg.get("data") or {}
            if kind == "audio":
                audio = base64.b64decode(data.get("audio") or "")
                if self.first_audio_ms is None and audio:
                    self.first_audio_ms = round((time.monotonic() - self._opened) * 1000)
                    logger.info("sarvam_ws_first_audio", ms=self.first_audio_ms, connect_ms=self.connect_ms)
                for i in range(0, len(audio), _PLAYBACK_CHUNK):
                    self.audio_chunks_received += 1
                    yield base64.b64encode(audio[i : i + _PLAYBACK_CHUNK]).decode()
            elif kind == "event" and data.get("event_type") == "final":
                self.final_seen = True
                return
            elif kind == "error":
                self._fail("sarvam_ws_error")
                return
