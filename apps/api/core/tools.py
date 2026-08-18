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
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import failover as voice_failover
from apps.api.channels.whatsapp import client as wa_client
from apps.api.config import settings
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.models.appointment import Appointment
from apps.api.db.models.conversation import Conversation
from apps.api.db.models.follow_up import FollowUpTask
from apps.api.db.models.lead import Lead
from apps.api.db.models.org import Org
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
                    "date": {
                        "type": "string",
                        "description": "Requested appointment date (ISO 8601, e.g. 2025-06-01).",
                    },
                    "time": {
                        "type": "string",
                        "description": "Requested appointment time (HH:MM, 24-hour).",
                    },
                    "timezone": {
                        "type": "string",
                        "description": (
                            "IANA timezone name (e.g. 'America/New_York', 'Europe/London') "
                            "that date/time above are in. Defaults to IST (Asia/Kolkata) — "
                            "only set this if the caller explicitly asks to book in a "
                            "different timezone. This only affects this one booking, not "
                            "any default going forward."
                        ),
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes or reason for the appointment.",
                    },
                },
                "required": ["date", "time"],
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
    {
        "type": "function",
        "function": {
            "name": "send_whatsapp_message",
            "description": (
                "Send a WhatsApp text message to the caller — use this when someone on a "
                "call (or in WhatsApp chat) asks to receive information (pricing, links, "
                "confirmations, etc.) in writing over WhatsApp. If they don't give a "
                "different number, it goes to their own number automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message text to send over WhatsApp.",
                    },
                    "phone": {
                        "type": "string",
                        "description": (
                            "Phone number to send to (E.164 preferred). Omit to use the "
                            "current caller's own number."
                        ),
                    },
                    "appointment_date": {
                        "type": "string",
                        "description": (
                            "Only when this message is confirming an appointment you just "
                            "booked with book_appointment: the appointment's date, e.g. "
                            "'Aug 13, 2026'. Lets delivery fall back to an approved template "
                            "if the caller has no open WhatsApp session with us (the normal "
                            "case for a phone call) — omit for any other kind of message."
                        ),
                    },
                    "appointment_time": {
                        "type": "string",
                        "description": (
                            "Only when confirming an appointment: the appointment's time "
                            "with timezone, e.g. '5:35 PM IST'. Pass together with "
                            "appointment_date, never alone."
                        ),
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "initiate_ai_call",
            "description": (
                "Place an outbound AI voice call to the caller — use this when someone "
                "chatting over WhatsApp asks to be called instead of continuing over text. "
                "If they don't give a different number, it calls their own number "
                "automatically. Not usable while already on a voice call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": (
                            "Phone number to call (E.164 preferred). Omit to use the "
                            "current caller's own number."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief reason for the call, for context/logging.",
                    },
                },
                "required": [],
            },
        },
    },
]


# Redis key prefixes — keep names stable, the dashboard reads them too.
_HANDOFF_QUEUE_KEY = "human_handoff_queue"
_LEAD_DEDUPE_PREFIX = "veerox:lead_dedupe:"
_LEAD_DEDUPE_TTL_SECS = 10 * 60  # 10 minutes per spec §2.3

# All bookings are interpreted in IST unless the caller explicitly asks for a
# different timezone on that specific booking (see book_appointment's
# ``timezone`` arg) — the org has no configured locale of its own, so this is
# the fixed default rather than something derived from Org/User rows.
DEFAULT_BOOKING_TIMEZONE = "Asia/Kolkata"


def _default_org_id() -> UUID:
    """Cast the settings string to a UUID once per call site."""
    return UUID(settings.default_org_id)


def _normalize_phone(phone: str) -> str:
    """Strip non-digit characters except a leading ``+`` for E.164 friendliness."""
    cleaned = re.sub(r"[^\d+]", "", phone or "")
    return cleaned


def _format_display_time(time_str: str) -> str:
    """Render a 24-hour ``HH:MM`` time (the LLM's tool-call format) as
    12-hour clock time for customer-facing WhatsApp templates, e.g.
    ``"15:00"`` -> ``"3:00 PM"``. Falls back to the raw value if it doesn't
    parse, so a malformed time still reaches the template instead of
    crashing the send."""
    try:
        return datetime.strptime(time_str, "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return time_str


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
    org_id: UUID | None = None,
    channel: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Persist a new lead. Idempotent on ``(org_id, phone, intent)`` for 10 min.

    Idempotency uses Redis ``SET NX EX`` — a duplicate call within the window
    returns ``status="duplicate"`` with no row written. ``org_id`` is caller
    context injected by the dispatch layer (the call/message's already-
    resolved tenant); falls back to the platform default only if the caller
    genuinely has no org context to give (e.g. CLI/test invocations).
    """
    org_id = org_id or _default_org_id()
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


APPOINTMENT_CONFLICT_WINDOW_MINUTES = 30


async def find_conflicting_appointment(
    db: AsyncSession,
    org_id: UUID,
    scheduled_at: datetime,
    exclude_appointment_id: UUID | None = None,
) -> Appointment | None:
    """Return an existing appointment within ``APPOINTMENT_CONFLICT_WINDOW_MINUTES``
    of ``scheduled_at`` for this org, or ``None`` if the slot is free.

    Cancelled/no-show appointments have freed up their slot, so they're
    excluded from the check.
    """
    window = timedelta(minutes=APPOINTMENT_CONFLICT_WINDOW_MINUTES)
    stmt = (
        select(Appointment)
        .where(
            Appointment.org_id == org_id,
            Appointment.scheduled_at > scheduled_at - window,
            Appointment.scheduled_at < scheduled_at + window,
            Appointment.status.notin_(("cancelled", "no_show")),
        )
        .limit(1)
    )
    if exclude_appointment_id is not None:
        stmt = stmt.where(Appointment.id != exclude_appointment_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def book_appointment(
    db: AsyncSession,
    date: str,
    time: str,
    timezone: str | None = None,
    notes: str | None = None,
    user_id: UUID | None = None,
    org_id: UUID | None = None,
    channel: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Record a booking: a ``Lead`` (intent='booking', for the CRM timeline)
    plus an ``Appointment`` row (for the Appointments page/calendar).

    ``user_id``/``org_id`` are caller context injected by the agent dispatch
    layer (``core/agent.py``'s ``_dispatch_tool`` and the voice adapter's
    ``_dispatch_realtime_tool``) — they must NOT also be properties on this
    tool's LLM-facing schema above, or the injected kwarg and the LLM's own
    argument collide (``TypeError: got multiple values for argument
    'user_id'``), which previously made every booking silently fail before
    any row was written. ``org_id`` is the call/message's already-resolved
    tenant; without threading it through, every booking landed in the
    platform default org regardless of which org's number was actually
    called, invisible to every other org's dashboard.

    ``date``/``time`` come from the LLM per the tool schema above — ISO 8601
    date, HH:MM 24-hour time. ``timezone`` is the IANA zone that pair should
    be read in; it defaults to IST (``DEFAULT_BOOKING_TIMEZONE``) and only
    ever applies to this one booking — there's no per-org/per-user override
    stored anywhere, by design (the caller says "book it for 3pm Berlin
    time" once, not "change my timezone"). The local wall-clock time is
    resolved in that zone, then converted to UTC for storage — ``Appointment
    .scheduled_at`` stays UTC regardless of what zone the caller booked in.
    Malformed date/time/timezone values fail the booking outright rather
    than silently landing on the CRM with no calendar entry.
    """
    if user_id is None:
        return {"status": "error", "reason": "missing_user_id"}

    org_id = org_id or _default_org_id()
    booking_user_id = user_id
    booking_user = await db.get(User, booking_user_id)

    tz_name = timezone or DEFAULT_BOOKING_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return {"status": "error", "reason": "invalid_timezone"}

    try:
        local_dt = datetime.fromisoformat(f"{date}T{time}").replace(tzinfo=tz)
    except ValueError:
        return {"status": "error", "reason": "invalid_date_or_time"}
    scheduled_at = local_dt.astimezone(UTC)

    if scheduled_at <= datetime.now(UTC):
        # Caught here rather than left to the LLM's judgment — the model has
        # no reliable sense of "now" on its own and will otherwise happily
        # book a plausible-sounding past date/time straight onto the
        # calendar. Returned as a normal tool error so the agent's next turn
        # can ask the caller for a different, future time instead of the
        # booking silently landing in the past.
        return {"status": "error", "reason": "date_in_past"}

    if await find_conflicting_appointment(db, org_id, scheduled_at) is not None:
        # Keeps appointments at least APPOINTMENT_CONFLICT_WINDOW_MINUTES apart
        # org-wide — e.g. a 2:00 PM booking blocks 2:29 PM. Checked before any
        # row is written so a rejected slot leaves no partial Lead/Appointment.
        return {"status": "error", "reason": "slot_conflict"}

    metadata: dict[str, Any] = {
        "date": date,
        "time": time,
        "timezone": tz_name,
        "notes": notes,
        "booked_at": datetime.now(UTC).isoformat(),
    }

    lead = Lead(
        org_id=org_id,
        user_id=booking_user_id,
        name=booking_user.name if booking_user else None,
        phone=booking_user.phone if booking_user else None,
        intent="booking",
        channel=channel,
        metadata_=metadata,
    )
    db.add(lead)
    await db.flush()  # populate lead.id for the Appointment FK below

    appointment = Appointment(
        org_id=org_id,
        lead_id=lead.id,
        scheduled_at=scheduled_at,
        notes=notes,
    )
    db.add(appointment)

    # Schedule reminders at each offset before the appointment, sent by the
    # follow-up dispatcher (workers/follow_up_dispatcher.py) via the same
    # pre-approved template used for the immediate confirmation below — it
    # works outside the 24h free-text session window, which a reminder sent
    # hours after booking usually is. Offsets that have already passed by
    # booking time (e.g. booking 10 minutes out) are skipped rather than
    # firing immediately.
    reminder_params = [
        booking_user.name if booking_user and booking_user.name else "there",
        date,
        _format_display_time(time),
    ]
    now = datetime.now(UTC)
    for offset_minutes in _APPOINTMENT_REMINDER_OFFSETS_MINUTES:
        reminder_at = scheduled_at - timedelta(minutes=offset_minutes)
        if reminder_at <= now:
            continue
        db.add(
            FollowUpTask(
                org_id=org_id,
                lead_id=lead.id,
                rule_id=None,
                run_at=reminder_at,
                status="pending",
                template_name=_APPOINTMENT_TEMPLATE_NAME,
                template_params=reminder_params,
            )
        )

    await db.commit()

    logger.info(
        "book_appointment_persisted",
        lead_id=str(lead.id),
        appointment_id=str(appointment.id),
        user_id=str(booking_user_id),
        date=date,
        time=time,
        timezone=tz_name,
    )

    # Auto-notify the caller on WhatsApp so the booking is confirmed even on
    # channels (voice) where the agent's spoken reply is the only other
    # confirmation. Sent straight via the pre-approved appointment_confirmation
    # template (not plain text) so it reaches the person even when we've
    # never messaged them before and no 24h session is open — the common
    # case for a call-initiated booking. Best-effort: a failed send must not
    # undo the booking that already committed above.
    target_phone = booking_user.phone if booking_user else None
    if target_phone:
        org = await db.get(Org, org_id)
        phone_number_id = org.whatsapp_phone_number_id if org else None
        try:
            await wa_client.send_template(
                _normalize_phone(target_phone),
                _APPOINTMENT_TEMPLATE_NAME,
                body_params=[
                    booking_user.name if booking_user and booking_user.name else "there",
                    date,
                    _format_display_time(time),
                ],
                phone_number_id=phone_number_id,
            )
        except httpx.HTTPError:
            logger.warning(
                "book_appointment_confirmation_send_error",
                appointment_id=str(appointment.id),
                exc_info=True,
            )
    else:
        logger.warning(
            "book_appointment_confirmation_send_failed",
            appointment_id=str(appointment.id),
            reason="no_phone_number_available",
        )

    return {
        "status": "ok",
        "lead_id": str(lead.id),
        "appointment_id": str(appointment.id),
        "date": date,
        "time": time,
        "timezone": tz_name,
    }


async def transfer_to_human(
    db: AsyncSession,
    reason: str,
    urgency: str = "medium",
    user_id: UUID | None = None,
    org_id: UUID | None = None,
    channel: str | None = None,
    conversation_id: UUID | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Escalate to a human: enqueue in Redis and write an escalation ``Lead``.

    The ``Lead`` row is only written when the agent layer supplies a
    ``user_id`` (the LLM args don't carry one). When absent, the Redis
    enqueue still happens so operators see the request on the dashboard.
    ``conversation_id`` is likewise agent-layer context (see module
    docstring) — threaded through so the Escalations dashboard can link
    straight to the transcript.
    """
    org_id = org_id or _default_org_id()
    redis = get_redis_pool()

    phone: str | None = None
    if user_id is not None:
        user = await db.get(User, user_id)
        phone = user.phone if user is not None else None

    payload = {
        "reason": reason,
        "urgency": urgency,
        "user_id": str(user_id) if user_id else None,
        "org_id": str(org_id),
        "channel": channel,
        "phone": phone,
        "conversation_id": str(conversation_id) if conversation_id else None,
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
            phone=phone,
            conversation_id=conversation_id,
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
    org_id: UUID | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Look up a user by phone within the caller's org.

    Returns the user's name and most-recent conversation timestamp, or
    ``{"found": False}`` when no row matches.
    """
    org_id = org_id or _default_org_id()
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


def _meta_error_code(exc: httpx.HTTPStatusError) -> int | None:
    try:
        body = exc.response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error.get("code") if isinstance(error, dict) else None


# Meta error 131047: "Re-engagement message" — the recipient has no open
# 24h customer-service session (the common case for a call-initiated send,
# since a phone call never opens one), so free-form text is rejected.
_REENGAGEMENT_ERROR_CODE = 131047

_APPOINTMENT_TEMPLATE_NAME = "appointment_confirmation"

# Reminders fire this many minutes before the appointment (see
# book_appointment) — 1 hour, 30 minutes, and 5 minutes out.
_APPOINTMENT_REMINDER_OFFSETS_MINUTES = (60, 30, 5)


async def send_whatsapp_message(
    db: AsyncSession,
    message: str,
    phone: str | None = None,
    user_id: UUID | None = None,
    org_id: UUID | None = None,
    channel: str | None = None,
    appointment_date: str | None = None,
    appointment_time: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Send a free-form WhatsApp text to the caller, from either channel.

    Lets the AI act on "send that to me on WhatsApp" mid-call, and lets a
    WhatsApp conversation send a message to a *different* number the caller
    names. ``phone`` is optional: when omitted, falls back to the current
    caller's own number (resolved via ``user_id``) — the common case.

    Free-form text only works inside Meta's 24h customer-service window
    (the recipient must have messaged us recently). A call never opens that
    window, so a voice caller's first WhatsApp send is normally *outside*
    it and Meta rejects it with error 131047 ("re-engagement message"). When
    that happens and the caller supplied ``appointment_date``/
    ``appointment_time`` (i.e. this is a booking confirmation), we retry via
    the pre-approved ``appointment_confirmation`` template, which — being
    Meta-approved — is allowed to reach someone with no open session. Any
    other free-form send outside the window still surfaces as
    ``status="error"`` so the model can tell the caller a human follow-up is
    needed instead.
    """
    org_id = org_id or _default_org_id()

    target_phone = phone
    caller: User | None = None
    if user_id is not None:
        caller = await db.get(User, user_id)
    if not target_phone:
        target_phone = caller.phone if caller else None

    if not target_phone:
        return {"status": "error", "reason": "no_phone_number_available"}

    normalized = _normalize_phone(target_phone)
    org = await db.get(Org, org_id)
    phone_number_id = org.whatsapp_phone_number_id if org else None

    try:
        await wa_client.send_text(normalized, message, phone_number_id=phone_number_id)
    except httpx.HTTPStatusError as exc:
        if (
            _meta_error_code(exc) == _REENGAGEMENT_ERROR_CODE
            and appointment_date
            and appointment_time
        ):
            try:
                await wa_client.send_template(
                    normalized,
                    _APPOINTMENT_TEMPLATE_NAME,
                    body_params=[
                        caller.name if caller and caller.name else "there",
                        appointment_date,
                        _format_display_time(appointment_time),
                    ],
                    phone_number_id=phone_number_id,
                )
            except httpx.HTTPError as exc2:
                logger.warning(
                    "send_whatsapp_message_template_fallback_failed",
                    phone=normalized,
                    error=str(exc2),
                )
                return {"status": "error", "reason": "whatsapp_send_failed"}
            logger.info(
                "send_whatsapp_message_tool_ok",
                phone=normalized,
                org_id=str(org_id),
                via="template",
            )
            return {"status": "ok", "phone": normalized, "via": "template"}

        logger.warning(
            "send_whatsapp_message_tool_failed",
            phone=normalized,
            error=str(exc),
        )
        return {"status": "error", "reason": "whatsapp_send_failed"}
    except httpx.HTTPError as exc:
        logger.warning(
            "send_whatsapp_message_tool_failed",
            phone=normalized,
            error=str(exc),
        )
        return {"status": "error", "reason": "whatsapp_send_failed"}

    logger.info("send_whatsapp_message_tool_ok", phone=normalized, org_id=str(org_id), via="text")
    return {"status": "ok", "phone": normalized}


async def initiate_ai_call(
    db: AsyncSession,
    phone: str | None = None,
    reason: str | None = None,
    user_id: UUID | None = None,
    org_id: UUID | None = None,
    channel: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Place an outbound AI voice call to the caller, from a WhatsApp chat.

    Refuses on the voice channel itself (already on a call). Reuses the same
    Plivo/Twilio failover ``routers/admin.py``'s ``outbound_call`` endpoint
    uses, dialing from the org's own number when it has one.
    """
    if channel == "voice":
        return {"status": "error", "reason": "already_on_a_call"}

    org_id = org_id or _default_org_id()

    target_phone = phone
    if not target_phone and user_id is not None:
        caller = await db.get(User, user_id)
        target_phone = caller.phone if caller else None

    if not target_phone:
        return {"status": "error", "reason": "no_phone_number_available"}

    if not voice_failover.is_configured():
        return {"status": "error", "reason": "calling_not_configured"}

    normalized = _normalize_phone(target_phone)
    org = await db.get(Org, org_id)
    plivo_from = f"+{org.plivo_phone_number}" if org and org.plivo_phone_number else None
    twilio_from = f"+{org.twilio_phone_number}" if org and org.twilio_phone_number else None

    answer_url = f"{settings.public_base_url.rstrip('/')}/voice/answer?org_id={org_id}"
    try:
        _result, provider = await voice_failover.initiate_call(
            normalized,
            answer_url,
            plivo_from_number=plivo_from,
            twilio_from_number=twilio_from,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "initiate_ai_call_tool_failed",
            phone=normalized,
            error=str(exc),
        )
        return {"status": "error", "reason": "call_failed"}

    logger.info(
        "initiate_ai_call_tool_ok", phone=normalized, org_id=str(org_id), provider=provider
    )
    return {"status": "ok", "phone": normalized, "provider": provider}


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
    "send_whatsapp_message": send_whatsapp_message,
    "initiate_ai_call": initiate_ai_call,
}
