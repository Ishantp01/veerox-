"""Recovering from a text-to-speech failure without leaving the caller in silence.

What can go wrong with ElevenLabs mid-call: the socket will not open, it opens but
never produces audio, it drops, or it rejects the org's voice settings (the error
arrives as a message on the socket, not as an exception). Because the OpenAI
Realtime session is running in text-only mode while ElevenLabs is the voice, a
failed turn means the caller hears nothing.

The recovery strategy, in order, each step bounded:

1. **Retry once with safe settings** - the platform voice/model and default voice
   settings, which are known to work, replaying the turn's text so far. This covers
   a rejected profile and a transient connect error.
2. **Pre-generated apology** - a short fixed message synthesized EARLIER in the call
   (while ElevenLabs was still working), played from memory. Played through the same
   audio path as every other reply, so it is in the right format by construction.
3. **Controlled end** - hang the call up through the provider, record the outcome and
   raise a human follow-up so the customer is called back. If no apology audio exists
   the call is ended without one: a short disconnect is better than silence.

Dynamic switching to OpenAI native audio mid-call is deliberately NOT done: the
Realtime session was configured for text-only output, and changing its output
modalities mid-session is unverified. Likewise the provider's own text-to-speech
(Plivo Speak / Twilio TwiML) is not used: for Twilio, redirecting the call would
tear down the media stream. Both are listed as possible future work, not done blind.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict

import structlog

from apps.api.channels.voice import elevenlabs_client
from apps.api.channels.voice.greeting import synthesize_greeting
from apps.api.channels.voice.voice_profile import ResolvedVoice, platform_model_id, platform_voice_id
from apps.api.config import settings

logger = structlog.get_logger(__name__)

# Hinglish, like the rest of the call. Kept short: it is played at the worst moment.
APOLOGY = "Maaf kijiye, abhi hamare system mein kuch dikkat aa gayi hai. Hum aapko jaldi dobara call karenge. Dhanyavaad."
FOLLOW_UP_REASON = (
    "The voice system failed during this call and it was ended. Please call the customer back."
)


class FallbackAudioCache:
    """The apology audio for each voice, synthesized once, in memory.

    Keyed by the voice's audio fingerprint, so an org with its own voice/model gets an
    apology in that voice and no org ever plays another org's audio. Filled in the
    background the first time a call successfully opens a session with that voice."""

    def __init__(self, max_entries: int = 32) -> None:
        self._entries: OrderedDict[str, tuple[str, ...]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._max = max_entries

    @staticmethod
    def key(voice: ResolvedVoice | None) -> str:
        if voice is not None:
            return voice.audio_fingerprint()
        raw = f"platform|{platform_voice_id()}|{platform_model_id()}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, voice: ResolvedVoice | None) -> tuple[str, ...] | None:
        key = self.key(voice)
        hit = self._entries.get(key)
        if hit is not None:
            self._entries.move_to_end(key)
        return hit

    def ensure_in_background(self, voice: ResolvedVoice | None) -> None:
        """Start filling the entry unless present/in flight. Never raises, never blocks."""
        if not (settings.voice_tts_recovery and elevenlabs_client.is_configured()):
            return
        key = self.key(voice)
        if key in self._entries or key in self._inflight:
            return

        async def _fill() -> None:
            try:
                chunks = await synthesize_greeting(APOLOGY, voice)
                if chunks:
                    self._entries[key] = tuple(chunks)
                    while len(self._entries) > self._max:
                        self._entries.popitem(last=False)
                    logger.info("voice_fallback_audio_cached", chunks=len(chunks))
            finally:
                self._inflight.pop(key, None)

        try:
            self._inflight[key] = asyncio.get_running_loop().create_task(_fill())
        except RuntimeError:
            return

    def clear(self) -> None:
        self._entries.clear()
        for task in self._inflight.values():
            task.cancel()
        self._inflight.clear()


fallback_audio = FallbackAudioCache()

