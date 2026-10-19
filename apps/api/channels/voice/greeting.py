"""Opening greeting for voice calls: per-org text, AI disclosure, audio cache.

Three concerns, all backward compatible:

* **Text** — ``Org.voice_greeting`` (NULL for every existing org) is spoken
  verbatim when set. Without it the call opens exactly as it always has: the
  model is asked for a short warm Hindi greeting "adapted to the persona and
  script". The only differences are the brand, which now comes from one place
  (``settings.voice_brand_name``) instead of a literal in the bridge that
  disagreed with the prompt, and an AI-disclosure clause that
  ``VOICE_GREETING_AI_DISCLOSURE=false`` removes again.
* **Disclosure** — a greeting must tell the caller they are talking to an AI.
  ``validate_greeting`` enforces that for text an org saves, so a custom
  greeting cannot silently drop it.
* **Audio cache** (opt-in, ``VOICE_GREETING_AUDIO_CACHE``) — a custom greeting
  is fixed text, so its audio can be synthesized once and replayed instead of
  paying LLM + TTS time before the first word on every call. Entries are keyed
  by org, text, voice and TTS model and live in this process's memory only;
  any miss or failure falls back to the normal spoken greeting.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from apps.api.channels.voice import elevenlabs_client
from apps.api.channels.voice.voice_profile import ResolvedVoice
from apps.api.config import settings

logger = structlog.get_logger(__name__)

MAX_GREETING_CHARS = 300

# What counts as telling the caller it is an AI. "AI" is matched case-
# sensitively on purpose: case-insensitively it would also hit Hinglish words.
_DISCLOSURE_RE = re.compile(
    r"\bAI\b|\bA\.I\.|(?i:artificial|virtual assistant|digital assistant|automated|\bbot\b)"
    r"|एआई|ए\s?आई|ए\.आई|बॉट|वर्चुअल|कृत्रिम"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class GreetingError(ValueError):
    """The submitted greeting text can't be used."""


def has_ai_disclosure(text: str) -> bool:
    return bool(_DISCLOSURE_RE.search(text))


def validate_greeting(text: str | None, *, require_disclosure: bool | None = None) -> str | None:
    """Normalise and validate an org greeting. ``None``/blank clears it.

    Raises ``GreetingError`` (a ``ValueError``) with a message safe to show.
    """
    if text is None:
        return None
    cleaned = " ".join(text.split())
    if not cleaned:
        return None
    if _CONTROL_RE.search(text):
        raise GreetingError("Greeting contains unsupported control characters.")
    if len(cleaned) > MAX_GREETING_CHARS:
        raise GreetingError(f"Greeting is too long ({len(cleaned)} > {MAX_GREETING_CHARS} characters).")
    needs = settings.voice_greeting_ai_disclosure if require_disclosure is None else require_disclosure
    if needs and not has_ai_disclosure(cleaned):
        raise GreetingError(
            "Greeting must tell the caller they are speaking to an AI assistant "
            '(for example "...main <company> ka AI assistant hoon...").'
        )
    return cleaned


def default_greeting_example(brand: str, ai_disclosure: bool) -> str:
    if ai_disclosure:
        return f"Namaste, main {brand} ka AI assistant Veer bol raha hoon. Main aapki kya madad kar sakta hoon?"
    return f"Namaste, {brand} se Veer bol raha hoon. Main aapki kya madad kar sakta hoon?"


def effective_brand(org: Any) -> str:
    """The brand spoken on this org's calls: its own ``voice_brand`` if set, else the legacy platform
    brand. Orgs that existed before ``Org.voice_brand`` have NULL, so they keep the brand they always
    had; new orgs are created with ``settings.voice_new_org_brand``."""
    own = getattr(org, "voice_brand", None) if org is not None else None
    return own or settings.voice_brand_name


def greeting_instruction(
    org_greeting: str | None, brand: str, ai_disclosure: bool, notice: str | None = None
) -> str:
    """The ``response.create`` instruction that opens the call. ``notice`` is the org's spoken
    recording notice (policy mode "notice"), said once right after the greeting."""
    if org_greeting:
        spoken = (
            "Begin the call now by saying exactly this greeting, word for word, in a "
            f"natural voice: {org_greeting!r}."
        )
        if notice:
            return (
                f"{spoken} Then, straight after it, say exactly this notice: {notice!r}. Say nothing else "
                "and add nothing before or after, then stop and wait for the caller."
            )
        return spoken + " Say nothing else and add nothing before or after it, then stop and wait for the caller."
    # Same wording the bridge always used; only the example line and the
    # optional disclosure sentence vary.
    instruction = (
        "Begin the call now: open with a short, warm, natural greeting in Hindi - "
        f"something like {default_greeting_example(brand, ai_disclosure)!r} - adapted to "
        "match your persona and script. "
    )
    if ai_disclosure:
        instruction += "The greeting must make clear that you are an AI assistant. "
    if notice:
        instruction += f"Right after the greeting, say once, briefly: {notice!r}. "
    return instruction + (
        "Do not ask which language they want - just start the conversation in Hindi."
    )


# ---------------------------------------------------------------------------
# Audio cache
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CachedGreeting:
    text: str
    chunks: tuple[str, ...]  # base64 mu-law 8 kHz, as ElevenLabs returned them


Synth = Callable[..., Awaitable[list[str]]]

_SYNTH_TIMEOUT_SECONDS = 8.0


async def synthesize_greeting(text: str, voice: ResolvedVoice | None = None) -> list[str]:
    """One ElevenLabs streaming-TTS turn for ``text``; [] on any failure.
    ``voice`` is the org's resolved profile (None = platform voice)."""
    try:
        session_cm = (
            elevenlabs_client.ElevenLabsTTSSession(profile=voice)
            if voice is not None
            else elevenlabs_client.ElevenLabsTTSSession()
        )
        async with session_cm as session:
            await session.send_text(text)
            await session.finish()

            async def _collect() -> list[str]:
                return [chunk async for chunk in session.audio_chunks()]

            return await asyncio.wait_for(_collect(), timeout=_SYNTH_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 — a cache fill must never affect a call
        logger.warning("voice_greeting_synth_failed", exc_info=True)
        return []


class GreetingAudioCache:
    def __init__(self, max_entries: int = 64, synth: Synth | None = None) -> None:
        self._entries: OrderedDict[str, CachedGreeting] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._max = max_entries
        self._synth = synth or synthesize_greeting

    @staticmethod
    def key(org_id: UUID | str, text: str, voice: ResolvedVoice | None = None) -> str:
        # Voice and model are part of the key: changing either must not replay
        # audio recorded in the old voice. With an org voice profile the whole
        # profile fingerprint (voice, model, voice settings, speech
        # normalization, pronunciations) is part of it, so changing speed or a
        # respelling also invalidates the cached audio.
        if voice is None:
            raw = f"{org_id}|{settings.elevenlabs_voice_id}|{settings.elevenlabs_model_id}|{text}"
        else:
            raw = f"{org_id}|profile:{voice.audio_fingerprint()}|{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(
        self, org_id: UUID | str, text: str, voice: ResolvedVoice | None = None
    ) -> CachedGreeting | None:
        key = self.key(org_id, text, voice)
        hit = self._entries.get(key)
        if hit is not None:
            self._entries.move_to_end(key)
        return hit

    def ensure_in_background(
        self, org_id: UUID | str, text: str, voice: ResolvedVoice | None = None
    ) -> None:
        """Start filling the entry for ``text`` unless it exists or is already
        being filled. Never raises, never blocks."""
        if not (settings.voice_greeting_audio_cache and elevenlabs_client.is_configured()):
            return
        key = self.key(org_id, text, voice)
        if key in self._entries or key in self._inflight:
            return

        async def _fill() -> None:
            try:
                chunks = await (self._synth(text) if voice is None else self._synth(text, voice))
                if chunks:
                    self._entries[key] = CachedGreeting(text=text, chunks=tuple(chunks))
                    while len(self._entries) > self._max:
                        self._entries.popitem(last=False)
                    logger.info("voice_greeting_cached", org_id=str(org_id), chunks=len(chunks))
            finally:
                self._inflight.pop(key, None)

        try:
            self._inflight[key] = asyncio.get_running_loop().create_task(_fill())
        except RuntimeError:  # no running loop (sync caller): nothing to do
            return

    def clear(self) -> None:
        self._entries.clear()
        for task in self._inflight.values():
            task.cancel()
        self._inflight.clear()


greeting_cache = GreetingAudioCache()
