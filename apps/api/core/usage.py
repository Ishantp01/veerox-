"""Reusable usage-metering entry point — every billable/measurable operation
in the app (a call, a WhatsApp message, an LLM completion, a worker tick)
funnels through `record_usage` to append one immutable `UsageEvent` row.

Idempotency: `event_id` is caller-supplied and must be deterministic for
anything that can retry (a call, a webhook delivery, a worker tick) — see
`deterministic_event_id` below. `record_usage` relies on the DB's unique
constraint on `UsageEvent.event_id` to make a duplicate insert a no-op
(catches the `IntegrityError` from the race, not a check-then-insert, so
it's correct even under concurrent callers), rather than trusting callers to
de-duplicate themselves.

Security: `metadata` is checked against a small denylist of keys that must
never be persisted (API keys, tokens, message bodies, ...) — defense in
depth on top of callers simply not passing them. Never store raw provider
payloads, call audio, or personal data here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.usage_event import USAGE_SERVICES, USAGE_TYPES, UsageEvent

logger = structlog.get_logger(__name__)

# Fixed uuid5 namespace so `deterministic_event_id` is stable across
# processes/deploys (a random namespace per-process would defeat the point).
_EVENT_ID_NAMESPACE = uuid.UUID("6f1c2a2e-2b1a-4f0a-9c2e-3a1b2c3d4e5f")

# Keys that must never reach `UsageEvent.metadata_json`, regardless of what
# a caller passes — req §5 ("never store API keys, access tokens, message
# bodies, call audio, unnecessary personal data, authorization headers,
# sensitive provider payloads").
_METADATA_DENYLIST = {
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "auth_token",
    "password",
    "secret",
    "client_secret",
    "message_body",
    "body",
    "audio",
    "recording_url",
}


class UsageMetadataViolation(ValueError):
    """Raised when `metadata` carries a denylisted key — a programming
    error in the caller, not a runtime condition to swallow."""


def deterministic_event_id(*parts: str) -> str:
    """A stable, globally-unique event id for a retryable operation.

    Same `parts` always produces the same id (uuid5, not uuid4), so a retry
    of the same call/webhook/job tick that calls `record_usage` again is
    silently ignored by the DB's unique constraint on `event_id` instead of
    double-counted. Callers should include every value that makes the
    occurrence unique (e.g. `deterministic_event_id("voice_call_ended",
    call_uuid)`, `deterministic_event_id("whatsapp_inbound", wamid)`).
    """
    joined = "|".join(parts)
    return str(uuid.uuid5(_EVENT_ID_NAMESPACE, joined))


def _check_metadata(metadata: dict[str, Any] | None) -> None:
    if not metadata:
        return
    hits = _METADATA_DENYLIST.intersection(k.lower() for k in metadata)
    if hits:
        raise UsageMetadataViolation(
            f"usage metadata must not contain sensitive keys: {sorted(hits)}"
        )


async def record_usage(
    db: AsyncSession,
    *,
    organization_id: UUID,
    event_id: str,
    service: str,
    usage_type: str,
    quantity: Decimal | float | int,
    unit: str,
    source: str,
    provider: str | None = None,
    request_id: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> UsageEvent | None:
    """Append one usage event. Returns the created row, or `None` if
    `event_id` already exists (a retry — not an error).

    Raises `ValueError`/`UsageMetadataViolation` for a malformed call
    (unknown `service`/`usage_type`, sensitive metadata key) — those are
    programming errors, not conditions to silently absorb.
    """
    if service not in USAGE_SERVICES:
        raise ValueError(f"unknown usage service: {service!r}")
    if usage_type not in USAGE_TYPES:
        raise ValueError(f"unknown usage_type: {usage_type!r}")
    _check_metadata(metadata)

    event = UsageEvent(
        event_id=event_id,
        org_id=organization_id,
        request_id=request_id,
        service=service,
        usage_type=usage_type,
        quantity=Decimal(str(quantity)),
        unit=unit,
        provider=provider,
        source=source,
        started_at=started_at,
        ended_at=ended_at,
        metadata_json=metadata,
    )
    # SAVEPOINT (not a full db.rollback()) so a duplicate event_id only
    # unwinds this one insert — matches workers/follow_up_dispatcher.py's
    # per-row dedup pattern, safe when the caller has other pending changes
    # in the same outer transaction (e.g. recording usage alongside the
    # Conversation/Message write it's measuring).
    try:
        async with db.begin_nested():
            db.add(event)
            await db.flush()
    except IntegrityError:
        logger.info("usage_event_duplicate_ignored", event_id=event_id, org_id=str(organization_id))
        return None
    return event
