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
from types import TracebackType
from typing import Any, AsyncIterator

import structlog
import websockets

from apps.api.config import settings

logger = structlog.get_logger(__name__)

_ELEVENLABS_WS_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"


def is_configured() -> bool:
    return bool(settings.elevenlabs_api_key)


class ElevenLabsTTSSession:
    """One streaming-TTS turn — open, push text as it arrives, read audio
    back concurrently, then finish. Not reusable across turns; open a new
    one per assistant response."""

    def __init__(
        self,
        voice_id: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self._voice_id = voice_id or settings.elevenlabs_voice_id
        self._model_id = model_id or settings.elevenlabs_model_id
        self._ws: Any = None
        self._audio_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "ElevenLabsTTSSession":
        url = (
            _ELEVENLABS_WS_URL.format(voice_id=self._voice_id)
            + f"?model_id={self._model_id}&output_format=ulaw_8000"
        )
        self._ws = await websockets.connect(url)
        # Required first message on ElevenLabs' streaming-input endpoint —
        # a lone space primes the connection; the API key travels in the
        # body rather than a header since not every WS client can set
        # custom headers, and this is ElevenLabs' documented pattern.
        await self._ws.send(
            json.dumps(
                {
                    "text": " ",
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
                    "xi_api_key": settings.elevenlabs_api_key,
                }
            )
        )
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
                        error=msg.get("error"),
                        message=msg.get("message"),
                    )
                    break
                audio_b64 = msg.get("audio")
                if audio_b64:
                    await self._audio_queue.put(audio_b64)
                if msg.get("isFinal"):
                    break
        except websockets.ConnectionClosed as exc:
            if exc.code != 1000:
                # 1000 = normal closure (we asked it to via `finish()`).
                # Anything else means ElevenLabs rejected/dropped the
                # connection — usually accompanies the error message above,
                # but log the raw close code/reason too in case it doesn't.
                logger.warning(
                    "elevenlabs_tts_connection_closed", code=exc.code, reason=exc.reason
                )
        except Exception:  # noqa: BLE001
            logger.warning("elevenlabs_tts_reader_error", exc_info=True)
        finally:
            await self._audio_queue.put(None)

    async def send_text(self, text: str) -> None:
        """Push one text chunk (e.g. one ``response.text.delta``) in."""
        if not text or self._ws is None:
            return
        await self._ws.send(json.dumps({"text": text}))

    async def finish(self) -> None:
        """Signal no more text is coming for this turn — flushes ElevenLabs'
        internal buffer so it generates audio for whatever's left."""
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"text": ""}))

    async def audio_chunks(self) -> AsyncIterator[str]:
        """Yield base64 mu-law audio chunks as they arrive, until the turn
        finishes (``isFinal``) or the connection closes."""
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                return
            yield chunk

