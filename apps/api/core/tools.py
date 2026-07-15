"""Tool definitions and handler implementations for the agent loop.

Each handler takes ``db: AsyncSession`` as its first positional argument so
the agent loop can inject a session at call time. Optional ``user_id`` is
also accepted as a kwarg by every handler so the agent layer can pass
caller context where the LLM args alone are insufficient (most notably
``transfer_to_human``, where neither phone nor user_id is in the schema).

Handlers are idempotent where possible — see the Redis-backed dedupe in
``capture_lead`` — per the "Idempotency on tool calls" guidance in
``longrunning/operations/pitfalls.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.models.conversation import Conversation
from apps.api.db.models.lead import Lead
from apps.api.db.models.user import User
from apps.api.redis_client import get_redis_pool

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool JSON Schemas — surfaced to the LLM via tool_choice="auto".
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "capture_lead",
            "description": "Capture a lead's contact information and intent for follow-up.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Full name of the lead."},
                    "phone": {"type": "string", "description": "Phone number of the lead."},
                    "intent": {
                        "type": "string",
                        "description": "The lead's primary intent or reason for contact.",
                    },
                },
                "required": ["phone", "intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book an appointment for the caller at a requested date and time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "UUID of the user booking."},
                    "date": {
                        "type": "string",
                        "description": "Requested appointment date (ISO 8601, e.g. 2025-06-01).",
                    },
                    "time": {
                        "type": "string",
                        "description": "Requested appointment time (HH:MM, 24-hour).",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes or reason for the appointment.",
                    },
                },
                "required": ["user_id", "date", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_to_human",
            "description": (
                "Transfer the conversation to a human agent when the AI cannot resolve "
                "the query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Reason for escalation to a human agent.",
                    },
                    "urgency": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Urgency level of the transfer.",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qualify_lead",
            "description": (
                "Record the qualification verdict for a prospect on an outbound calling "
                "campaign, once you have asked enough questions to judge them against the "
                "stated criteria. Call this exactly once, near the end of the call, "
                "regardless of whether the prospect qualifies or not."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "interested": {
                        "type": "boolean",
                        "description": "Whether the prospect meets the criteria and is interested.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of the verdict, referencing the criteria.",
                    },
                    "name": {
                        "type": "string",
                        "description": "The prospect's name, if given during the call.",
                    },
                },
                "required": ["interested", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_customer",
            "description": (
                "Look up an existing customer by phone number to retrieve their profile."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Phone number to look up (E.164 format preferred).",
                    },
                },
                "required": ["phone"],
            },
        },
    },
]


# Redis key prefixes — keep names stable, the dashboard reads them too.
_HANDOFF_QUEUE_KEY = "human_handoff_queue"
_LEAD_DEDUPE_PREFIX = "veerox:lead_dedupe:"
_LEAD_DEDUPE_TTL_SECS = 10 * 60  # 10 minutes per spec §2.3


def _default_org_id() -> UUID:
    """Cast the settings string to a UUID once per call site."""
    return UUID(settings.default_org_id)


def _normalize_phone(phone: str) -> str:
    """Strip non-digit characters except a leading ``+`` for E.164 friendliness."""
    cleaned = re.sub(r"[^\d+]", "", phone or "")
    return cleaned


def _lead_dedupe_key(org_id: UUID, phone: str, intent: str) -> str:
    """Stable Redis key for the ``(org_id, phone, intent)`` idempotency tuple."""
    digest = hashlib.sha256(
        f"{org_id}|{_normalize_phone(phone)}|{intent.strip().lower()}".encode()
    ).hexdigest()
    return f"{_LEAD_DEDUPE_PREFIX}{digest}"


async def _get_or_create_user_by_phone(
    db: AsyncSession,
    org_id: UUID,
    phone: str,
    name: str | None = None,
) -> User:
    """Resolve a user by ``(org_id, phone)``, creating a stub row if missing.

    The composite unique constraint on ``users(org_id, phone)`` keeps this
    safe under concurrent first-contact events; on collision SQLAlchemy will
    surface an IntegrityError which the caller can choose to handle.
    """
    normalized = _normalize_phone(phone)
    stmt = select(User).where(User.org_id == org_id, User.phone == normalized)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        if name and not existing.name:
            existing.name = name
        return existing

    user = User(org_id=org_id, phone=normalized, name=name)
    db.add(user)
    await db.flush()  # populate user.id without committing the outer transaction
    return user


# ---------------------------------------------------------------------------
# Handlers — each takes ``db`` first, then LLM-supplied args, plus an
# optional ``user_id`` kwarg the agent layer may inject for caller context.
# ---------------------------------------------------------------------------


async def capture_lead(
    db: AsyncSession,
    phone: str,
    intent: str,
    name: str | None = None,
    user_id: UUID | None = None,
    channel: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Persist a new lead. Idempotent on ``(org_id, phone, intent)`` for 10 min.

    Idempotency uses Redis ``SET NX EX`` — a duplicate call within the window
    returns ``status="duplicate"`` with no row written.
    """
    org_id = _default_org_id()
    redis = get_redis_pool()
    key = _lead_dedupe_key(org_id, phone, intent)

    acquired = await redis.set(key, "1", nx=True, ex=_LEAD_DEDUPE_TTL_SECS)
    if not acquired:
        logger.info(
            "capture_lead_duplicate_suppressed",
            org_id=str(org_id),
            phone=_normalize_phone(phone),
            intent=intent,
        )
        return {"status": "duplicate", "reason": "recent_lead_for_phone_intent"}

    user = await _get_or_create_user_by_phone(
        db, org_id=org_id, phone=phone, name=name
    )
    # Prefer the explicit user_id passed by the agent (caller context) when present.
    effective_user_id = user_id or user.id

    lead = Lead(
        org_id=org_id,
        user_id=effective_user_id,
        name=name or user.name,
        phone=_normalize_phone(phone),
        intent=intent,
        channel=channel,
    )
    db.add(lead)
    await db.commit()

    logger.info(
        "capture_lead_persisted",
        lead_id=str(lead.id),
        org_id=str(org_id),
        user_id=str(effective_user_id),
        intent=intent,
    )
    return {"status": "ok", "lead_id": str(lead.id)}


async def book_appointment(
    db: AsyncSession,
    user_id: str,
    date: str,
    time: str,
    notes: str | None = None,
    channel: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Record a booking by writing to ``Lead.metadata`` with ``intent='booking'``.

    Intentionally does not create an ``Appointment`` table — that scope is
    deferred to Day 5 per implementation plan §2.3.
    """
    org_id = _default_org_id()
    try:
        booking_user_id = UUID(user_id)
    except (ValueError, TypeError):
        return {"status": "error", "reason": "invalid_user_id"}

    metadata: dict[str, Any] = {
        "date": date,
        "time": time,
        "notes": notes,
        "booked_at": datetime.now(UTC).isoformat(),
    }

    lead = Lead(
        org_id=org_id,
        user_id=booking_user_id,
        intent="booking",
        channel=channel,
        metadata_=metadata,
    )
    db.add(lead)
    await db.commit()

    logger.info(
        "book_appointment_persisted",
        lead_id=str(lead.id),
        user_id=str(booking_user_id),
        date=date,
        time=time,
    )
    return {
        "status": "ok",
        "lead_id": str(lead.id),
        "date": date,
        "time": time,
    }


async def transfer_to_human(
    db: AsyncSession,
    reason: str,
    urgency: str = "medium",
    user_id: UUID | None = None,
    channel: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Escalate to a human: enqueue in Redis and write an escalation ``Lead``.

    The ``Lead`` row is only written when the agent layer supplies a
    ``user_id`` (the LLM args don't carry one). When absent, the Redis
    enqueue still happens so operators see the request on the dashboard.
    """
    org_id = _default_org_id()
    redis = get_redis_pool()

    payload = {
        "reason": reason,
        "urgency": urgency,
        "user_id": str(user_id) if user_id else None,
        "org_id": str(org_id),
        "channel": channel,
        "requested_at": datetime.now(UTC).isoformat(),
    }
    # redis-py stubs type rpush as `Awaitable[int] | int` because the same
    # method exists on the sync client — the async client always returns the
    # awaitable, so the await is correct at runtime.
    await redis.rpush(_HANDOFF_QUEUE_KEY, json.dumps(payload))  # type: ignore[misc]

    lead_id: str | None = None
    if user_id is not None:
        lead = Lead(
            org_id=org_id,
            user_id=user_id,
            intent="escalation",
            channel=channel,
            metadata_={"reason": reason, "urgency": urgency},
        )
        db.add(lead)
        await db.commit()
        lead_id = str(lead.id)

    logger.info(
        "transfer_to_human_enqueued",
        org_id=str(org_id),
        user_id=str(user_id) if user_id else None,
        urgency=urgency,
        lead_id=lead_id,
    )

    return {
        "status": "ok",
        "message": "I'm connecting you to a human agent, please hold.",
        "lead_id": lead_id,
    }


async def qualify_lead(
    db: AsyncSession,
    interested: bool,
    reason: str,
    name: str | None = None,
    user_id: UUID | None = None,
    campaign_target_id: UUID | None = None,
    channel: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Record a campaign call's qualification verdict.

    Only meaningful mid-campaign-call — ``campaign_target_id`` is injected by
    the voice adapter from call context, never supplied by the LLM. Writes a
    ``Lead`` row only when ``interested`` is true, so uninterested prospects
    never reach the CRM.
    """
    if campaign_target_id is None:
        logger.warning("qualify_lead_no_campaign_target")
        return {"status": "error", "reason": "no_campaign_target"}

    target = await db.get(CampaignTarget, campaign_target_id)
    if target is None:
        return {"status": "error", "reason": "campaign_target_not_found"}

    target.status = "completed"
    target.qualified = interested
    target.disposition_reason = reason

    lead_id: str | None = None
    if interested:
        org_id = target.org_id
        resolved_name = name or target.name
        user = await _get_or_create_user_by_phone(
            db, org_id=org_id, phone=target.phone, name=resolved_name
        )
        lead = Lead(
            org_id=org_id,
            user_id=user_id or user.id,
            name=resolved_name,
            phone=_normalize_phone(target.phone),
            intent="qualified_campaign_lead",
            channel=channel,
            status="qualified",
            metadata_={"campaign_id": str(target.campaign_id), "reason": reason},
        )
        db.add(lead)
        await db.flush()
        lead_id = str(lead.id)

    await db.commit()

    logger.info(
        "qualify_lead_recorded",
        campaign_target_id=str(campaign_target_id),
        interested=interested,
        lead_id=lead_id,
    )
    return {"status": "ok", "interested": interested, "lead_id": lead_id}


async def lookup_customer(
    db: AsyncSession,
    phone: str,
    **_: Any,
) -> dict[str, Any]:
    """Look up a user by phone within the default org.

    Returns the user's name and most-recent conversation timestamp, or
    ``{"found": False}`` when no row matches.
    """
    org_id = _default_org_id()
    normalized = _normalize_phone(phone)

    user_stmt = select(User).where(User.org_id == org_id, User.phone == normalized)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if user is None:
        return {"found": False}

    conv_stmt = (
        select(Conversation.started_at)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.started_at.desc())
        .limit(1)
    )
    last_seen = (await db.execute(conv_stmt)).scalar_one_or_none()

    return {
        "found": True,
        "user_id": str(user.id),
        "name": user.name,
        "phone": user.phone,
        "last_conversation_at": last_seen.isoformat() if last_seen else None,
    }


# ---------------------------------------------------------------------------
# Dispatch table — looked up by the agent loop. Handlers expect ``db`` first
# and accept ``user_id`` as an optional kwarg the agent may inject.
# ---------------------------------------------------------------------------


ToolHandler = Callable[..., Awaitable[dict[str, Any]]]

DISPATCH_TABLE: dict[str, ToolHandler] = {
    "capture_lead": capture_lead,
    "book_appointment": book_appointment,
    "transfer_to_human": transfer_to_human,
    "qualify_lead": qualify_lead,
    "lookup_customer": lookup_customer,
}
