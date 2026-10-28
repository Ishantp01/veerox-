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
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, AsyncIterator

import httpx
import structlog
import websockets

from apps.api.channels.voice.tts_normalize import TTSNormalizer
from apps.api.channels.voice.voice_profile import (
    DEFAULT_STYLE,
    LEGACY_VOICE_SETTINGS,
    STYLE_MODELS,
    ResolvedVoice,
    platform_chunk_schedule,
    platform_model_id,
    platform_voice_id,
    resolve_profile,
)
from apps.api.config import settings

logger = structlog.get_logger(__name__)

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


def error_code(msg: dict[str, Any]) -> str:
    """A short machine-readable code from a provider error message, never its text.

    The text can name a voice id."""
    detail = msg.get("detail")
    for candidate in (msg.get("error"), msg.get("code"), msg.get("status"),
                      detail.get("status") if isinstance(detail, dict) else None):
        if isinstance(candidate, str) and _CODE_RE.match(candidate):
            return candidate
    return "provider_error"


_ELEVENLABS_WS_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"


def is_configured() -> bool:
    return bool(settings.elevenlabs_api_key)


@dataclass(frozen=True)
class VoiceCheck:
    """``ok`` True: the voice exists. False: the provider refused it. None: could not tell."""

    ok: bool | None
    reason: str


_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{10,40}$")
_VOICE_CHECK_TTL_SECONDS = 600.0
_voice_check_cache: dict[str, tuple[float, VoiceCheck]] = {}


async def validate_voice(voice_id: str, *, client: Any = None) -> VoiceCheck:
    """Ask ElevenLabs (a free metadata lookup, no synthesis) whether ``voice_id`` exists.

    Only a definitive answer is cached (10 minutes). A network error or a 5xx is "could not
    tell", never "invalid"."""
    if not _VOICE_ID_RE.match(voice_id or ""):
        return VoiceCheck(False, "malformed_voice_id")
    cached = _voice_check_cache.get(voice_id)
    if cached is not None and time.monotonic() - cached[0] < _VOICE_CHECK_TTL_SECONDS:
        return cached[1]
    if not settings.elevenlabs_api_key:
        return VoiceCheck(False, "no_api_key")
    own = client is None
    http = httpx.AsyncClient(timeout=8.0) if own else client
    try:
        response = await http.get(
            f"https://api.elevenlabs.io/v1/voices/{voice_id}",
            headers={"xi-api-key": settings.elevenlabs_api_key},
        )
    except Exception:  # noqa: BLE001 - an unreachable provider must never block a call
        return VoiceCheck(None, "unreachable")
    finally:
        if own:
            await http.aclose()
    status = response.status_code
    if status == 200:
        result = VoiceCheck(True, "ok")
    elif status in (400, 404):
        result = VoiceCheck(False, "voice_not_found")
    elif status in (401, 403):
        result = VoiceCheck(False, "unauthorized")
    else:
        return VoiceCheck(None, f"http_{status}")
    _voice_check_cache[voice_id] = (time.monotonic(), result)
    return result


async def preflight_error(org_voice_profile: Any = None) -> str | None:
    """A refusal message when the provider definitively rejects the voice a call would use."""
    resolved = resolve_profile(org_voice_profile)
    voice_id = resolved.voice_id if resolved is not None else platform_voice_id()
    result = await validate_voice(voice_id)
    if result.ok is False:
        return f"ElevenLabs rejected the configured voice ({result.reason}); no call was placed."
    if result.ok is None:
        logger.warning("voice_preflight_unavailable", reason=result.reason)
    return None


# VOICE_TTS_PHRASE_FLUSH: boundaries that end a speakable phrase. Latin and Devanagari sentence
# ends always count; a comma/semicolon/colon counts only once the phrase is long enough to carry
# its own intonation.
_SENTENCE_ENDS = ".?!।…"
_SOFT_ENDS = ",;:"
_MIN_PHRASE_CHARS = 12
_MIN_SOFT_PHRASE_CHARS = 40
# A dot after one of these (or after a single letter, as in initials) is not the end of a sentence.
_ABBREVIATIONS = frozenset(
    {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "rs", "no", "vs", "etc", "approx", "min",
     "max", "a.m", "p.m", "i.e", "e.g", "shri", "smt", "sh"}
)


def _is_abbreviation(before: str) -> bool:
    """True when the text before a "." ends in a known abbreviation or a lone initial."""
    word = before.rsplit(None, 1)[-1].lower() if before.strip() else ""
    return word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha())


class PhraseBuffer:
    """Collects streamed text deltas and releases whole phrases.

    A phrase ends at sentence punctuation (or a comma once long enough) that is FOLLOWED BY
    WHITESPACE, so "3.5" is never split; a dot after an abbreviation ("Rs.", "Dr.") or an initial
    is skipped; and a very short fragment ("Haan.") is held and joined with the next phrase
    instead of being synthesised alone (tiny fragments lose intonation). ``flush`` returns the
    remainder.
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> str:
        self._buf += text
        cut = -1
        for i in range(len(self._buf) - 1):
            ch = self._buf[i]
            if not self._buf[i + 1].isspace():
                continue
            size = i + 1
            if ch in _SENTENCE_ENDS and size >= _MIN_PHRASE_CHARS:
                if ch == "." and _is_abbreviation(self._buf[:i]):
                    continue
                cut = i + 2
            elif ch in _SOFT_ENDS and size >= _MIN_SOFT_PHRASE_CHARS:
                cut = i + 2
        if cut < 0:
            return ""
        out, self._buf = self._buf[:cut], self._buf[cut:]
        return out

    def flush(self) -> str:
        out, self._buf = self._buf, ""
        return out


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
        # pre-profile behaviour: platform voice/model, stability 0.45 and
        # similarity_boost 0.80, no text rewriting.
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
        # Phrase-aware streaming (off unless VOICE_TTS_PHRASE_FLUSH): whole phrases are sent with
        # flush=true so the provider speaks each with full context instead of waiting on its
        # character threshold.
        self._phrases: PhraseBuffer | None = (
            PhraseBuffer() if settings.voice_tts_phrase_flush else None
        )
        self._ws: Any = None
        self._audio_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        # Milliseconds the WebSocket handshake took (None until connected);
        # exposed for latency instrumentation only.
        self.connect_ms: int | None = None
        # Monotonic moment the handshake was sent; a prewarmed socket older than the provider's idle timeout is stale.
        self.opened_at: float | None = None
        # Monotonic moments, for latency instrumentation only: the first text handed to this
        # session, and the first text actually sent to the provider. Their gap is how long phrase
        # buffering held the first words (zero when VOICE_TTS_PHRASE_FLUSH is off).
        self.first_text_at: float | None = None
        self.first_send_at: float | None = None
        # Failure and usage facts the adapter reads (Phase 5). ``failed`` is set when the provider
        # reports an error message or drops the socket before its final-audio marker; those arrive
        # as data, not exceptions, so without this a broken turn is just silence.
        self.failed = False
        self.fail_reason: str | None = None
        self.fail_code: str | None = None  # short code only, for diagnostics
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
        schedule = (self._profile.chunk_schedule if self._profile is not None else None) or platform_chunk_schedule()
        if schedule:
            # Only when the org's profile or VOICE_TTS_CHUNK_SCHEDULE asks for it; otherwise the handshake is
            # exactly what it always was.
            handshake["generation_config"] = {"chunk_length_schedule": list(schedule)}
        await self._send(json.dumps(handshake))
        self.opened_at = time.monotonic()
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
                    self.fail_code = error_code(msg)
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
                self.fail_code = f"closed_{exc.code}"
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
            self.fail_code = "reader_error"
            self.fail_reason = "reader_error"
        finally:
            await self._audio_queue.put(None)

    async def _send(self, payload: str) -> None:
        try:
            await self._ws.send(payload)
        except websockets.ConnectionClosed as exc:
            self.failed = True
            self.fail_code = "closed_during_send"
            self.fail_reason = "connection_closed"
            raise TTSConnectionClosed("ElevenLabs socket closed during send") from exc

    def _mark_first_send(self) -> None:
        if self.first_send_at is None:
            self.first_send_at = time.monotonic()

    async def send_text(self, text: str) -> None:
        """Push one text chunk (e.g. one ``response.text.delta``) in."""
        if not text or self._ws is None:
            return
        if self.first_text_at is None:
            self.first_text_at = time.monotonic()
        if self._normalizer is not None:
            text = self._normalizer.feed(text)
            if not text:
                return  # buffered until a clause boundary
        if self._phrases is not None:
            text = self._phrases.feed(text)
            if not text:
                return  # held until a phrase boundary
            self.chars_sent += len(text)
            self._mark_first_send()
            await self._send(json.dumps({"text": text, "flush": True}))
            return
        self.chars_sent += len(text)
        self._mark_first_send()
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
        if self._phrases is not None:
            rest = self._phrases.flush()
            if rest.strip():
                self._mark_first_send()
                self.chars_sent += len(rest)
                await self._send(json.dumps({"text": rest if rest.endswith(" ") else rest + " "}))
        await self._send(json.dumps({"text": ""}))

    async def audio_chunks(self) -> AsyncIterator[str]:
        """Yield base64 mu-law audio chunks as they arrive, until the turn
        finishes (``isFinal``) or the connection closes."""
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                return
            yield chunk

