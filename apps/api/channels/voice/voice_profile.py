"""Per-organization voice profile (ElevenLabs TTS settings + speaking options).

Stored as a nullable JSON document on ``Org.voice_profile``. **NULL (every
existing org) means "no profile": the call uses the platform settings and the
exact handshake it always sent** (``stability 0.5``, ``similarity_boost 0.8``,
``settings.elevenlabs_voice_id`` / ``settings.elevenlabs_model_id``).
``resolve_profile`` returns ``None`` in that case so callers can keep their
pre-existing code path untouched.

Provider facts this module relies on (all UNVERIFIED against a live account -
no ElevenLabs key was available when this was written; they follow the public
stream-input documentation as remembered, so each is kept conservative):

* ``voice_settings`` accepts ``stability``, ``similarity_boost``, ``style``,
  ``use_speaker_boost`` and ``speed`` (documented ranges 0-1, 0-1, 0-1, bool,
  0.7-1.2). ``style`` is only forwarded for models in ``STYLE_MODELS`` and
  ``speed`` only when explicitly set.
* The three allow-listed models are all documented for the WebSocket
  stream-input endpoint; ``eleven_multilingual_v2`` has noticeably higher
  latency than the two ``v2_5`` models and is for quality comparison, not a
  recommended live default.
* ``language_code`` and ``pronunciation_dictionary_locators`` are deliberately
  NOT sent to the provider. Pronunciation fixes are done by our own
  deterministic text layer (``tts_normalize.py``) instead.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from apps.api.config import settings

logger = structlog.get_logger(__name__)

# model id -> short note for the settings page / comparison table.
ALLOWED_MODELS: dict[str, str] = {
    "eleven_flash_v2_5": "lowest latency (platform default)",
    "eleven_turbo_v2_5": "low latency, usually better quality than flash",
    "eleven_multilingual_v2": "highest quality, slower - not recommended as a live default",
}
# Models for which ``style`` is forwarded (and accepted on save).
STYLE_MODELS = frozenset({"eleven_multilingual_v2"})

SPEED_RANGE = (0.7, 1.2)
LANGUAGES = ("hi", "en", "hinglish")
MAX_PRONUNCIATIONS = 50
MAX_PRONUNCIATION_KEY = 40
MAX_PRONUNCIATION_VALUE = 80

_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{10,40}$")
# Private-use characters are reserved by the normalizer as placeholders.
_BAD_TEXT_RE = re.compile(r"[\x00-\x1f\x7f\ue000-\uf8ff]")

# What a NULL profile sends — kept here so a test can pin it.
LEGACY_VOICE_SETTINGS: dict[str, float] = {"stability": 0.5, "similarity_boost": 0.8}


class VoiceProfileError(ValueError):
    """The submitted profile can't be used; the message is safe to show."""


class VoiceProfile(BaseModel):
    """What an org admin may set. Every field is optional; unset = platform
    default. Unknown fields are rejected, never silently ignored."""

    model_config = ConfigDict(extra="forbid")

    voice_id: str | None = Field(None, description="ElevenLabs voice id (alphanumeric, 10-40 chars).")
    model_id: str | None = Field(None, description=f"One of: {', '.join(ALLOWED_MODELS)}.")
    speed: float | None = Field(None, ge=SPEED_RANGE[0], le=SPEED_RANGE[1])
    stability: float | None = Field(None, ge=0.0, le=1.0)
    similarity_boost: float | None = Field(None, ge=0.0, le=1.0)
    style: float | None = Field(None, ge=0.0, le=1.0)
    use_speaker_boost: bool | None = None
    preferred_language: Literal["hi", "en", "hinglish"] | None = Field(
        None, description="Hint for number/date/time wording; not sent to the provider."
    )
    normalize_speech: bool = Field(
        False, description="Rewrite numbers/dates/times/amounts into spoken words before TTS."
    )
    pronunciations: dict[str, str] = Field(
        default_factory=dict,
        description="Exact whole-word respellings applied to text sent to TTS only.",
    )
    speaking_style: bool = Field(
        False, description="Add the human-like speaking-style guidance to the voice prompt."
    )

    @field_validator("voice_id")
    @classmethod
    def _voice_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not _VOICE_ID_RE.match(value):
            raise ValueError("voice_id must be 10-40 letters/digits (an ElevenLabs voice id)")
        return value

    @field_validator("model_id")
    @classmethod
    def _model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ALLOWED_MODELS:
            raise ValueError(f"unknown model_id {value!r}; allowed: {', '.join(ALLOWED_MODELS)}")
        return value

    @field_validator("pronunciations")
    @classmethod
    def _pronunciations(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_PRONUNCIATIONS:
            raise ValueError(f"at most {MAX_PRONUNCIATIONS} pronunciation entries")
        out: dict[str, str] = {}
        for key, spoken in value.items():
            k, v = key.strip(), spoken.strip()
            if not k or len(k) > MAX_PRONUNCIATION_KEY:
                raise ValueError(f"pronunciation word must be 1-{MAX_PRONUNCIATION_KEY} characters")
            if not v or len(v) > MAX_PRONUNCIATION_VALUE:
                raise ValueError(f"pronunciation for {k!r} must be 1-{MAX_PRONUNCIATION_VALUE} characters")
            if _BAD_TEXT_RE.search(k) or _BAD_TEXT_RE.search(v):
                raise ValueError("pronunciation entries contain unsupported characters")
            out[k] = v
        return out

    def effective_model(self) -> str:
        return self.model_id or settings.elevenlabs_model_id

    def check_cross_fields(self) -> None:
        if self.style and self.effective_model() not in STYLE_MODELS:
            raise VoiceProfileError(
                "style is only supported with model eleven_multilingual_v2 "
                f"(effective model is {self.effective_model()!r}); set style to 0/omit it or change the model"
            )


def parse_profile(data: Any) -> VoiceProfile:
    """Validate raw JSON (e.g. from a stored column or a request) into a
    profile; raises ``VoiceProfileError`` with a readable message."""
    try:
        profile = VoiceProfile.model_validate(data)
    except ValidationError as exc:
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "profile"
            parts.append(f"{loc}: {err['msg']}")
        raise VoiceProfileError("; ".join(parts)) from exc
    profile.check_cross_fields()
    return profile


@dataclass(frozen=True)
class ResolvedVoice:
    """A profile merged over the platform settings for one call. Immutable and
    built per call from that call's org - never stored in a module global."""

    voice_id: str
    model_id: str
    voice_settings: dict[str, Any]
    normalize: bool = False
    language: str | None = None
    pronunciations: tuple[tuple[str, str], ...] = ()
    speaking_style: bool = False
    fingerprint: str = field(default="", compare=False)

    def audio_fingerprint(self) -> str:
        """Everything that changes the synthesized audio of a fixed text."""
        return self.fingerprint


def resolve_profile(raw: Any) -> ResolvedVoice | None:
    """Merge a stored profile over the platform settings.

    ``None``/empty -> ``None`` (legacy behaviour, byte for byte). A stored
    document that no longer validates is logged and treated as no profile, so
    a bad row can never fail a call.
    """
    if not raw:
        return None
    try:
        profile = parse_profile(raw)
    except VoiceProfileError as exc:
        logger.warning("voice_profile_invalid_ignored", error=str(exc))
        return None
    model_id = profile.effective_model()
    voice_settings: dict[str, Any] = {
        "stability": profile.stability if profile.stability is not None else LEGACY_VOICE_SETTINGS["stability"],
        "similarity_boost": (
            profile.similarity_boost
            if profile.similarity_boost is not None
            else LEGACY_VOICE_SETTINGS["similarity_boost"]
        ),
    }
    if profile.style is not None and model_id in STYLE_MODELS:
        voice_settings["style"] = profile.style
    if profile.use_speaker_boost is not None:
        voice_settings["use_speaker_boost"] = profile.use_speaker_boost
    if profile.speed is not None:
        voice_settings["speed"] = profile.speed
    voice_id = profile.voice_id or settings.elevenlabs_voice_id
    pron = tuple(sorted(profile.pronunciations.items()))
    digest = hashlib.sha256(
        json.dumps(
            [voice_id, model_id, voice_settings, profile.normalize_speech, profile.preferred_language, pron],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return ResolvedVoice(
        voice_id=voice_id,
        model_id=model_id,
        voice_settings=voice_settings,
        normalize=profile.normalize_speech,
        language=profile.preferred_language,
        pronunciations=pron,
        speaking_style=profile.speaking_style,
        fingerprint=digest,
    )


def speaking_style_enabled(raw: Any) -> bool:
    """Prompt-side switch; False for no/invalid profile."""
    resolved = resolve_profile(raw)
    return bool(resolved and resolved.speaking_style)
