"""ElevenLabs streaming TTS — optional swap for OpenAI Realtime's native
speech synthesis (see ``config.Settings.voice_tts_provider``).

When enabled, ``realtime_bridge.py`` puts the OpenAI Realtime session in
text-only output mode — it still owns speech-to-text, reasoning, tool
calls, turn detection, and barge-in; only the "speak the reply out loud"
step moves here. One ``ElevenLabsTTSSession`` is opened per assistant
turn: text deltas from ``response.text.delta`` are pushed in as they
arrive, and the resulting mu-law 8kHz audio chunks are read back
concurrently (ElevenLabs starts streaming audio before all the text has
been sent, not only after) via a background reader task feeding a queue.

Requesting ``output_format=ulaw_8000`` on the endpoint gets mu-law 8kHz
audio directly from ElevenLabs — the same format Plivo/Twilio/OpenAI all
use elsewhere in this codebase — so no resampling step is needed here
either.

UNVERIFIED: written against ElevenLabs' documented WebSocket streaming API
with no live account to test against. Confirm the message shapes below
(especially the initial handshake and the `isFinal` framing) against a
real connection before relying on this in production.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, AsyncIterator

import structlog
import websockets

from apps.api.channels.voice.tts_normalize import TTSNormalizer
from apps.api.channels.voice.voice_profile import (
    DEFAULT_STYLE,
    LEGACY_VOICE_SETTINGS,
    STYLE_MODELS,
    ResolvedVoice,
    platform_model_id,
    platform_voice_id,
)
from apps.api.config import settings

logger = structlog.get_logger(__name__)

_ELEVENLABS_WS_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"


def is_configured() -> bool:
    return bool(settings.elevenlabs_api_key)


class TTSConnectionClosed(RuntimeError):
    """An ElevenLabs transport failure, never an OpenAI session closure."""


class ElevenLabsTTSSession:
    """One streaming-TTS turn — open, push text as it arrives, read audio
    back concurrently, then finish. Not reusable across turns; open a new
    one per assistant response."""

    def __init__(
        self,
        voice_id: str | None = None,
        model_id: str | None = None,
        profile: ResolvedVoice | None = None,
    ) -> None:
        # ``profile`` is the call's org voice profile merged over the platform
        # settings (voice_profile.resolve_profile). None = exactly the
        # pre-profile behaviour: platform voice/model, stability 0.42 and
        # similarity_boost 0.8, no text rewriting.
        self._profile = profile
        self._voice_id = voice_id or (profile.voice_id if profile else None) or platform_voice_id()
        self._model_id = model_id or (profile.model_id if profile else None) or platform_model_id()
        self._voice_settings: dict[str, Any] = (
            dict(profile.voice_settings) if profile else dict(LEGACY_VOICE_SETTINGS)
        )
        if not profile and self._model_id in STYLE_MODELS:
            self._voice_settings["style"] = DEFAULT_STYLE
        # Spoken-form rewriting of the text sent to TTS (off unless the org's
        # profile enables it); None means send text through untouched.
        self._normalizer: TTSNormalizer | None = (
            TTSNormalizer(
                enabled=True, language=profile.language, pronunciations=profile.pronunciations
            )
            if profile is not None and profile.normalize
            else None
        )
        self._ws: Any = None
        self._audio_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        # Milliseconds the WebSocket handshake took (None until connected);
        # exposed for latency instrumentation only.
        self.connect_ms: int | None = None
        # Failure and usage facts the adapter reads (Phase 5). ``failed`` is set when the provider
        # reports an error message or drops the socket before its final-audio marker; those arrive
        # as data, not exceptions, so without this a broken turn is just silence.
        self.failed = False
        self.fail_reason: str | None = None
        self.final_seen = False
        self.audio_chunks_received = 0
        self.chars_sent = 0
        # Called once, with the characters sent, when the session closes.
        self.on_close: Callable[[int], None] | None = None
        self._closed_reported = False

    async def __aenter__(self) -> "ElevenLabsTTSSession":
        url = (
            _ELEVENLABS_WS_URL.format(voice_id=self._voice_id)
            + f"?model_id={self._model_id}&output_format=ulaw_8000"
        )
        started = time.monotonic()
        self._ws = await websockets.connect(url)
        self.connect_ms = round((time.monotonic() - started) * 1000)
        # Required first message on ElevenLabs' streaming-input endpoint —
        # a lone space primes the connection; the API key travels in the
        # body rather than a header since not every WS client can set
        # custom headers, and this is ElevenLabs' documented pattern.
        handshake: dict[str, Any] = {
            "text": " ",
            "voice_settings": self._voice_settings,
            "xi_api_key": settings.elevenlabs_api_key,
        }
        if self._profile is not None and self._profile.chunk_schedule:
            # Only for an org that asked for it; otherwise the handshake is exactly what it always was.
            handshake["generation_config"] = {"chunk_length_schedule": list(self._profile.chunk_schedule)}
        await self._send(json.dumps(handshake))
        self._reader_task = asyncio.create_task(self._read_audio())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._ws is not None:
            await self._ws.close()
        if self.on_close is not None and not self._closed_reported:
            self._closed_reported = True
            try:
                self.on_close(self.chars_sent)
            except Exception:  # noqa: BLE001 - accounting must never affect the call
                pass

    async def _read_audio(self) -> None:
        """Background task: forward each audio chunk ElevenLabs streams
        back onto the queue, terminated by a ``None`` sentinel. ElevenLabs
        reports request-level failures (bad voice_id, quota exceeded, auth
        errors, ...) as a JSON error message before closing the socket, not
        as a raised exception — surfacing it here is the difference between
        "the call goes silently dead" and an actionable log line."""
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if msg.get("error") or msg.get("message"):
                    logger.warning(
                        "elevenlabs_tts_error",
                        provider_error=True,
                    )
                    self.failed = True
                    self.fail_reason = str(msg.get("error") or msg.get("message"))[:200]
                    break
                audio_b64 = msg.get("audio")
                if audio_b64:
                    self.audio_chunks_received += 1
                    await self._audio_queue.put(audio_b64)
                if msg.get("isFinal"):
                    self.final_seen = True
                    break
        except websockets.ConnectionClosed as exc:
            if exc.code != 1000:
                self.failed = True
                self.fail_reason = f"connection_closed:{exc.code}"
                # 1000 = normal closure (we asked it to via `finish()`).
                # Anything else means ElevenLabs rejected/dropped the
                # connection — usually accompanies the error message above,
                # but log the raw close code/reason too in case it doesn't.
                logger.warning(
                    "elevenlabs_tts_connection_closed", code=exc.code, reason=exc.reason
                )
        except Exception:  # noqa: BLE001
            logger.warning("elevenlabs_tts_reader_error", exc_info=True)
            self.failed = True
            self.fail_reason = "reader_error"
        finally:
            await self._audio_queue.put(None)

    async def _send(self, payload: str) -> None:
        try:
            await self._ws.send(payload)
        except websockets.ConnectionClosed as exc:
            self.failed = True
            self.fail_reason = "connection_closed"
            raise TTSConnectionClosed("ElevenLabs socket closed during send") from exc

    async def send_text(self, text: str) -> None:
        """Push one text chunk (e.g. one ``response.text.delta``) in."""
        if not text or self._ws is None:
            return
        if self._normalizer is not None:
            text = self._normalizer.feed(text)
            if not text:
                return  # buffered until a clause boundary
        self.chars_sent += len(text)
        await self._send(json.dumps({"text": text}))

    async def finish(self) -> None:
        """Signal no more text is coming for this turn — flushes ElevenLabs'
        internal buffer so it generates audio for whatever's left."""
        if self._ws is None:
            return
        if self._normalizer is not None:
            tail = self._normalizer.flush()
            if tail:
                self.chars_sent += len(tail)
                await self._send(json.dumps({"text": tail}))
        await self._send(json.dumps({"text": ""}))

    async def audio_chunks(self) -> AsyncIterator[str]:
        """Yield base64 mu-law audio chunks as they arrive, until the turn
        finishes (``isFinal``) or the connection closes."""
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                return
            yield chunk

