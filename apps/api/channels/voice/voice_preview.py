"""Voice preview / comparison helpers: phrase set, one-shot synthesis, WAV.

Used by ``POST /admin/settings/voice-profile/preview`` and
``scripts/voice_compare.py``. Synthesis goes through the same
``ElevenLabsTTSSession`` the live calls use (so the voice profile, model and
speech normalization are exactly what a call would get). Nothing here runs
unless called; with no ``ELEVENLABS_API_KEY`` it raises ``PreviewUnavailable``
instead of attempting a connection.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from apps.api.channels.voice import elevenlabs_client
from apps.api.channels.voice.voice_profile import ResolvedVoice

PHRASES_PATH = Path(__file__).with_name("preview_phrases.json")
SAMPLE_RATE = 8000  # ulaw_8000

_PREVIEW_TIMEOUT_SECONDS = 20.0


class PreviewUnavailable(RuntimeError):
    """Preview can't run here (e.g. no ElevenLabs key)."""


class PreviewFailed(RuntimeError):
    """The provider returned an error or no audio."""


def load_phrases(path: Path | None = None) -> list[dict[str, str]]:
    data: dict[str, Any] = json.loads((path or PHRASES_PATH).read_text(encoding="utf-8"))
    return list(data["phrases"])


def phrase_text(phrase_id: str) -> str | None:
    for phrase in load_phrases():
        if phrase["id"] == phrase_id:
            return phrase["text"]
    return None


def ulaw_wav(ulaw: bytes) -> bytes:
    """Wrap raw mu-law 8 kHz mono audio in a WAV container (format tag 7)."""
    fmt = struct.pack("<HHIIHHH", 7, 1, SAMPLE_RATE, SAMPLE_RATE, 1, 8, 0)
    fact = struct.pack("<I", len(ulaw))
    body = (
        b"WAVE"
        + b"fmt " + struct.pack("<I", len(fmt)) + fmt
        + b"fact" + struct.pack("<I", len(fact)) + fact
        + b"data" + struct.pack("<I", len(ulaw)) + ulaw
        + (b"\x00" if len(ulaw) % 2 else b"")
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


@dataclass(frozen=True)
class SynthResult:
    audio: bytes  # raw mu-law 8 kHz
    connect_ms: int | None
    first_audio_ms: int  # from sending the text to the first audio chunk
    total_ms: int


async def synthesize(
    text: str,
    voice: ResolvedVoice | None = None,
    *,
    timeout: float = _PREVIEW_TIMEOUT_SECONDS,
    stream_words: bool = False,
) -> SynthResult:
    """Synthesize ``text`` through ElevenLabs with the given resolved voice
    (None = platform voice). Raises ``PreviewUnavailable`` without a key and
    ``PreviewFailed`` on timeout / provider error / no audio."""
    if not elevenlabs_client.is_configured():
        raise PreviewUnavailable("ElevenLabs is not configured (ELEVENLABS_API_KEY is not set)")
    chunks: list[bytes] = []
    first_ms: int | None = None
    try:
        session_cm = (
            elevenlabs_client.ElevenLabsTTSSession(profile=voice)
            if voice is not None
            else elevenlabs_client.ElevenLabsTTSSession()
        )
        async with session_cm as session:
            started = time.monotonic()
            if stream_words:
                # Deliver the text the way a live call does (a few characters at a time), so phrase
                # buffering and the provider's own chunking behave as they do on a call.
                for word in text.split(" "):
                    await session.send_text(word + " ")
            else:
                await session.send_text(text)
            await session.finish()

            async def _collect() -> None:
                nonlocal first_ms
                async for b64 in session.audio_chunks():
                    if first_ms is None:
                        first_ms = round((time.monotonic() - started) * 1000)
                    chunks.append(base64.b64decode(b64))

            await asyncio.wait_for(_collect(), timeout=timeout)
            total_ms = round((time.monotonic() - started) * 1000)
            connect_ms = session.connect_ms
    except asyncio.TimeoutError as exc:
        raise PreviewFailed("ElevenLabs did not finish in time") from exc
    except PreviewUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PreviewFailed(f"ElevenLabs request failed: {type(exc).__name__}") from exc
    audio = b"".join(chunks)
    if not audio or first_ms is None:
        raise PreviewFailed("ElevenLabs returned no audio (check the voice id and model)")
    return SynthResult(audio=audio, connect_ms=connect_ms, first_audio_ms=first_ms, total_ms=total_ms)


class RateLimiter:
    """Per-org sliding window, in-process (approximate across workers)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def allow(self, org_id: UUID | str, limit: int, window: float = 60.0, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self._hits.setdefault(str(org_id), deque())
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


preview_limiter = RateLimiter()
