"""Whisper transcription wrapper.

Lives under ``apps.api.core`` (not under a channel) because the brain may
want to reuse it later — e.g. voice-note cleanup for the WhatsApp channel
today and a Realtime fallback path tomorrow. Only this module imports
``openai`` for audio.
"""

from __future__ import annotations

import structlog
from openai import AsyncOpenAI

from apps.api.config import settings

logger = structlog.get_logger(__name__)


# Lazy-instantiated for connection pooling. Same reason as apps.api.core.llm:
# openai>=2.x raises at __init__ when no key is set, which would break tests
# and module imports in environments without an API key.
_client: AsyncOpenAI | None = None


def _get_client(api_key: str | None = None) -> AsyncOpenAI:
    """See core/llm.py::_get_client — same pooled-default/one-off-override split."""
    if api_key and api_key != settings.openai_api_key:
        return AsyncOpenAI(api_key=api_key)
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


# WhatsApp voice notes arrive as OGG/Opus from Meta. Other channels may pass
# their own MIME and a synthetic filename so the SDK's multipart upload sets
# the right Content-Type.
_MIME_TO_FILENAME = {
    "audio/ogg": "audio.ogg",
    "audio/ogg; codecs=opus": "audio.ogg",
    "audio/mpeg": "audio.mp3",
    "audio/mp4": "audio.m4a",
    "audio/wav": "audio.wav",
    "audio/webm": "audio.webm",
}


def _filename_for(mime: str) -> str:
    return _MIME_TO_FILENAME.get(mime.lower(), "audio.bin")


async def transcribe(
    audio_bytes: bytes,
    *,
    mime: str = "audio/ogg",
    language: str | None = None,
    api_key: str | None = None,
) -> str:
    """Transcribe an audio blob via OpenAI Whisper and return the text.

    Args:
        audio_bytes: Raw bytes downloaded from the source channel (e.g. Meta
            Cloud API media URL).
        mime: MIME type of the blob — used to pick a sensible upload filename
            so the SDK's multipart payload sets the right ``Content-Type``.
        language: Optional ISO-639-1 hint (``"hi"``, ``"en"``). Whisper
            auto-detects when unset, which is the right default for
            code-mixed Hindi/English speakers.
        api_key: Override for ``settings.openai_api_key`` — pass an org's own
            key (see core/org_openai_key.py::resolve_openai_api_key) to bill
            this call against it instead of the platform's shared key.

    Returns:
        The transcribed text. Empty string if Whisper returned no text.
    """
    filename = _filename_for(mime)
    file_tuple = (filename, audio_bytes, mime)

    # Explicit calls instead of dict-spread because the SDK overloads make
    # **dict[str, object] ambiguous to the type checker. The two branches
    # cover whether `language` is provided.
    if language:
        result = await _get_client(api_key).audio.transcriptions.create(
            model="whisper-1",
            file=file_tuple,
            language=language,
        )
    else:
        result = await _get_client(api_key).audio.transcriptions.create(
            model="whisper-1",
            file=file_tuple,
        )
    text = (getattr(result, "text", "") or "").strip()

    logger.info(
        "transcribe_completed",
        mime=mime,
        bytes=len(audio_bytes),
        language=language,
        chars=len(text),
    )
    return text
