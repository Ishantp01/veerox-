"""Instant micro-reaction ("जी...", "हूँ...") played the moment the caller stops talking.

Between the caller going quiet and the first TTS audio there is a dead gap (model first token + TTS first
byte). A short human "जी..." sent straight away covers it. Clips are synthesized once per voice through the
ElevenLabs REST endpoint as native ``ulaw_8000`` (the same format the call carries, so there is no conversion
step) and kept in memory as base64. Everything here is best-effort: a failure returns ``None`` and the call
simply has no reaction clip. Gated by ``VOICE_INSTANT_ACK`` (default off) in adapter.py.

UNVERIFIED against a live ElevenLabs account or a real call; judge by listening.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx
import structlog

from apps.api.config import settings

logger = structlog.get_logger(__name__)

CLIP_TEXTS = ("जी...", "हूँ...")
_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=ulaw_8000"
_SARVAM_URL = "https://api.sarvam.ai/text-to-speech"
_ULAW_BYTES_PER_SECOND = 8000
MAX_CLIP_SECONDS = 1.2  # a longer clip is not a micro-reaction; drop it

_cache: dict[tuple[str, str, str], str] = {}
_inflight: dict[tuple[str, str, str], asyncio.Task[Any]] = {}


def clips_for(voice_id: str, model_id: str) -> list[str]:
    """Base64 mu-law clips already synthesized for this voice/model (possibly none)."""
    return [c for t in CLIP_TEXTS if (c := _cache.get((voice_id, model_id, t)))]


SARVAM_PREFIX = "sarvam:"  # voice_id prefix: the clip is synthesized with Sarvam, so it matches the call's voice


async def _synthesize(voice_id: str, model_id: str, voice_settings: dict[str, Any], text: str) -> str | None:
    sarvam = voice_id.startswith(SARVAM_PREFIX)
    if not (settings.sarvam_api_key if sarvam else settings.elevenlabs_api_key):
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            if sarvam:
                resp = await client.post(
                    _SARVAM_URL,
                    headers={"api-subscription-key": settings.sarvam_api_key or ""},
                    json={
                        "text": text,
                        "target_language_code": settings.sarvam_language_code,
                        "speaker": voice_id[len(SARVAM_PREFIX):],
                        "model": model_id,
                        "pace": settings.sarvam_pace,
                        "speech_sample_rate": 8000,
                        "output_audio_codec": "mulaw",
                    },
                )
            else:
                resp = await client.post(
                    _URL.format(voice_id=voice_id),
                    headers={"xi-api-key": settings.elevenlabs_api_key},
                    json={"text": text, "model_id": model_id, "voice_settings": voice_settings},
                )
        resp.raise_for_status()
        audio = base64.b64decode(resp.json()["audios"][0]) if sarvam else resp.content
    except Exception as exc:  # noqa: BLE001 - never affects the call
        logger.warning("instant_ack_synth_failed", error_type=type(exc).__name__)
        return None
    if not audio or len(audio) / _ULAW_BYTES_PER_SECOND > MAX_CLIP_SECONDS:
        logger.warning("instant_ack_clip_rejected", bytes=len(audio or b""))
        return None
    return base64.b64encode(audio).decode("ascii")


async def _warm_one(voice_id: str, model_id: str, voice_settings: dict[str, Any], text: str) -> None:
    key = (voice_id, model_id, text)
    if key in _cache:
        return
    clip = await _synthesize(voice_id, model_id, voice_settings, text)
    if clip:
        _cache[key] = clip


def warm(voice_id: str, model_id: str, voice_settings: dict[str, Any]) -> None:
    """Start synthesizing any missing clips in the background. Safe to call on every turn."""
    for text in CLIP_TEXTS:
        key = (voice_id, model_id, text)
        task = _inflight.get(key)
        if key in _cache or (task is not None and not task.done()):
            continue
        _inflight[key] = asyncio.create_task(_warm_one(voice_id, model_id, dict(voice_settings), text))
