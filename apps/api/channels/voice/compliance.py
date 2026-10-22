"""Recording notice, consent and retention policy for voice calls.

The rules about recording a call differ by country (and, within some, by state),
and by whether the call is inbound or outbound. This module therefore makes no
legal judgement of its own: it lets each org say, per calling-code region, what
the agent does about recording, and it applies exactly that. Nothing is assumed
for an org that has configured nothing: ``Org.voice_compliance`` NULL resolves to
the long-standing behaviour (record every call, say nothing about it), so no
existing org's behaviour changes until an admin sets a policy. That default is a
compliance decision for the org to review, not a recommendation.

Modes (``recording_mode``):

* ``always``  - record automatically, no spoken notice (the legacy behaviour).
* ``notice``  - record automatically and tell the caller early in the call.
* ``consent`` - do NOT record; ask the caller, and start recording only if they
  clearly agree (the agent reports the answer through the ``recording_consent``
  tool). No answer means no recording.
* ``off``     - never record.

A policy can also carry ``retention_days``: how long this org keeps the stored
recording reference (see ``purge_expired_recordings``). The audio itself is hosted
by the telephony provider and is not deleted from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.conversation import Conversation

logger = structlog.get_logger(__name__)

RecordingMode = Literal["always", "notice", "consent", "off"]
MAX_REGIONS = 30
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Spoken when an org chose "notice" without wording of its own.
DEFAULT_NOTICE = "Yeh call quality aur training ke liye record ho sakti hai."

CONSENT_PROMPT = (
    "RECORDING CONSENT: this call must NOT be recorded unless the caller clearly agrees. Right after "
    "your greeting, ask once, briefly and politely, whether they are okay with the call being recorded "
    "for quality purposes. When they answer, call recording_consent with granted=true only for a "
    "clear yes, and granted=false for a no or anything unclear. Carry on normally either way; never "
    "pressure them and never ask again."
)


class RegionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recording_mode: RecordingMode | None = None
    recording_notice: str | None = Field(default=None, max_length=300)

    @field_validator("recording_notice")
    @classmethod
    def _clean_notice(cls, value: str | None) -> str | None:
        return _clean(value)


class VoiceCompliance(BaseModel):
    """What an org stores in ``Org.voice_compliance``. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    recording_mode: RecordingMode = "always"
    recording_notice: str | None = Field(default=None, max_length=300)
    # Calling-code prefix ("91", "1", "44") -> override. The longest matching prefix wins.
    regions: dict[str, RegionPolicy] = Field(default_factory=dict)
    retention_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("recording_notice")
    @classmethod
    def _clean_notice(cls, value: str | None) -> str | None:
        return _clean(value)

    @field_validator("regions")
    @classmethod
    def _check_regions(cls, value: dict[str, RegionPolicy]) -> dict[str, RegionPolicy]:
        if len(value) > MAX_REGIONS:
            raise ValueError(f"at most {MAX_REGIONS} regions are allowed")
        for key in value:
            if not re.fullmatch(r"\d{1,4}", key):
                raise ValueError(f"region {key!r} must be a calling code of 1-4 digits, e.g. '91'")
        return value


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    if _CONTROL_RE.search(value):
        raise ValueError("contains unsupported control characters")
    cleaned = " ".join(value.split())
    return cleaned or None


@dataclass(frozen=True)
class RecordingPolicy:
    mode: RecordingMode = "always"
    notice: str | None = None
    retention_days: int | None = None

    @property
    def records_automatically(self) -> bool:
        return self.mode in ("always", "notice")

    @property
    def spoken_notice(self) -> str | None:
        return (self.notice or DEFAULT_NOTICE) if self.mode == "notice" else None


LEGACY_POLICY = RecordingPolicy()


def _pilot_mode() -> bool:
    from apps.api import pilot

    return pilot.pilot_mode()


def validate(raw: Any) -> VoiceCompliance:
    """Parse a submitted policy; raises ``pydantic.ValidationError``."""
    return VoiceCompliance.model_validate(raw)


def resolve_recording_policy(raw: Any, remote_number: str | None) -> RecordingPolicy:
    """The policy for a call with ``remote_number``. Anything stored that no longer
    validates is logged and treated as no policy: a bad row must never fail a call."""
    if not raw:
        # Pilot mode (ENVIRONMENT=pilot) never records an org that has not chosen a policy; every other
        # environment keeps the long-standing "record every call" default.
        return RecordingPolicy(mode="off") if _pilot_mode() else LEGACY_POLICY
    try:
        config = VoiceCompliance.model_validate(raw)
    except ValidationError:
        logger.warning("voice_compliance_invalid_ignored")
        return RecordingPolicy(mode="off") if _pilot_mode() else LEGACY_POLICY
    mode, notice = config.recording_mode, config.recording_notice
    digits = re.sub(r"\D", "", remote_number or "").lstrip("0")
    best = max((code for code in config.regions if digits.startswith(code)), key=len, default=None)
    if best is not None:
        region = config.regions[best]
        mode = region.recording_mode or mode
        notice = region.recording_notice or notice
    return RecordingPolicy(mode=mode, notice=notice, retention_days=config.retention_days)


def recording_provider_and_id(url: str | None) -> tuple[str, str] | None:
    """(provider, recording id) from a stored recording URL, or None if it is not recognisable.
    Twilio: ``.../Recordings/RExxxx[.mp3]``; anything else is treated as Plivo, whose URL ends in the
    recording's id."""
    if not url:
        return None
    path = url.split("?", 1)[0].rstrip("/")
    last = path.rsplit("/", 1)[-1]
    recording_id = last.rsplit(".", 1)[0] if "." in last else last
    if not recording_id:
        return None
    return ("twilio" if "twilio.com" in path.lower() else "plivo", recording_id)


async def _delete_provider_audio(org: Any, url: str | None) -> str:
    """Ask the provider that hosts this recording to delete it, with the org's own credentials.
    ``"ok"``, ``"already_gone"``, ``"failed"`` or ``"skipped"`` (unrecognised URL / no credentials)."""
    from apps.api.channels.voice import plivo_client, twilio_client
    from apps.api.core.org_credentials import resolve_plivo_credentials, resolve_twilio_credentials

    parsed = recording_provider_and_id(url)
    if parsed is None:
        return "skipped"
    provider, recording_id = parsed
    if provider == "twilio":
        creds = resolve_twilio_credentials(org)
        return await twilio_client.delete_recording(creds, recording_id) if creds else "skipped"
    creds = resolve_plivo_credentials(org)
    return await plivo_client.delete_recording(creds, recording_id) if creds else "skipped"


async def purge_expired_recordings(
    db: AsyncSession, *, now: datetime | None = None, delete_provider_audio: bool | None = None
) -> int:
    """Clear the stored recording reference (URL + duration) of voice conversations that
    ended before their org's ``retention_days``. Org-scoped per row; orgs without a
    retention setting are never touched. Returns how many conversations were cleared.
    The audio is hosted by the telephony provider and is NOT deleted by this."""
    from apps.api.db.models.org import Org

    from apps.api.config import settings

    now = now or datetime.now(UTC)
    cleared = 0
    delete_audio = settings.voice_retention_delete_provider_audio if delete_provider_audio is None else delete_provider_audio
    orgs = (await db.execute(select(Org.id, Org.voice_compliance).where(Org.voice_compliance.is_not(None)))).all()
    for org_id, raw in orgs:
        policy = resolve_recording_policy(raw, None)
        if policy.retention_days is None:
            continue
        cutoff = now - timedelta(days=policy.retention_days)
        if delete_audio:
            # Delete the provider's copy first and forget our reference only when the provider confirms,
            # so a failed deletion is retried next run instead of orphaning audio nobody can find.
            org = await db.get(Org, org_id)
            expired = (
                await db.execute(
                    select(Conversation).where(
                        Conversation.org_id == org_id,
                        Conversation.channel == "voice",
                        Conversation.recording_url.is_not(None),
                        Conversation.ended_at.is_not(None),
                        Conversation.ended_at < cutoff,
                    )
                )
            ).scalars().all()
            for conversation in expired:
                outcome = await _delete_provider_audio(org, conversation.recording_url)
                if outcome in ("ok", "already_gone"):
                    conversation.recording_url = None
                    conversation.recording_duration_secs = None
                    cleared += 1
                else:
                    logger.warning("voice_retention_provider_delete_not_done", outcome=outcome,
                                   conversation_id=str(conversation.id))
            continue
        result = await db.execute(
            update(Conversation)
            .where(
                Conversation.org_id == org_id,
                Conversation.channel == "voice",
                Conversation.recording_url.is_not(None),
                Conversation.ended_at.is_not(None),
                Conversation.ended_at < cutoff,
            )
            .values(recording_url=None, recording_duration_secs=None)
        )
        cleared += result.rowcount or 0
    await db.commit()
    return cleared


async def record_consent_outcome(db: AsyncSession, conversation_id: UUID, org_id: UUID, granted: bool) -> None:
    """Tag the conversation with the caller's recording answer (org-checked)."""
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.org_id != org_id:
        return
    tag = f"recording:{'consented' if granted else 'declined'}"
    tags = [t for t in (conversation.tags or []) if not str(t).startswith("recording:")]
    conversation.tags = [*tags, tag]
    await db.commit()
