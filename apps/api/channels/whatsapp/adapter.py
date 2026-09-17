"""Bridge between the Meta inbound envelope and ``AgentCore``.

Runs as a FastAPI BackgroundTask after the webhook ACKs. Owns its own DB
session (the request-scoped session is gone by the time this runs) and is
wrapped in a try/except so a bad message never crashes the worker.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.whatsapp import client as wa_client
from apps.api.config import settings
from apps.api.core.agent import agent_core, appointment_booked_this_turn
from apps.api.core.org_credentials import resolve_meta_credentials
from apps.api.core.org_openai_key import resolve_openai_api_key
from apps.api.core.tools import mark_not_interested
from apps.api.core.transcribe import transcribe
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.models.org import Org
from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.db.models.user import User
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import get_redis_pool, record_error

logger = structlog.get_logger(__name__)


# Redis idempotency — Meta retries inbound webhooks aggressively. A 24h TTL
# is generous; the same wa_message_id will never be sent again that quickly.
_IDEMPOTENCY_KEY_PREFIX = "veerox:wa_msg:"
_IDEMPOTENCY_TTL_SECS = 24 * 60 * 60

# Fallback text we forward to the agent for unsupported message types so the
# model can answer gracefully ("I can only handle text and voice notes
# right now, please type your question").
_UNSUPPORTED_PLACEHOLDER = "(unsupported message type)"


@dataclass
class InboundMessage:
    """Flat view of the bits we care about from a Meta inbound envelope."""

    id: str
    from_phone: str
    type: str
    text: str | None = None
    media_id: str | None = None
    media_mime: str | None = None
    button_payload: str | None = None
    button_text: str | None = None


# Quick-reply button payload/text that means "stop automated follow-ups" —
# matched case-insensitively against whichever the template button was
# configured with, since a template's quick-reply payload defaults to the
# button's visible text when no custom payload was set at template creation.
_STOP_BUTTON_TRIGGERS = {"stop", "not interested", "unsubscribe"}


def _is_stop_button_tap(msg: "InboundMessage") -> bool:
    candidates = {(msg.button_payload or "").strip().lower(), (msg.button_text or "").strip().lower()}
    return bool(candidates & _STOP_BUTTON_TRIGGERS)


def _normalize_phone(phone: str) -> str:
    """Strip non-digit characters except a leading ``+`` for E.164 friendliness.

    Mirrors ``apps.api.core.tools._normalize_phone`` — duplicated rather than
    imported so the channel layer never reaches across into ``core``.
    """
    return re.sub(r"[^\d+]", "", phone or "")


def _extract_message(payload: dict[str, Any]) -> InboundMessage | None:
    """Pull the first message out of Meta's nested envelope.

    Returns ``None`` for status-only callbacks (delivery / read receipts)
    which have no ``messages[]`` array.
    """
    entries = payload.get("entry") or []
    for entry in entries:
        changes = entry.get("changes") or []
        for change in changes:
            value = change.get("value") or {}
            messages = value.get("messages") or []
            if not messages:
                continue
            msg = messages[0]
            msg_id = msg.get("id")
            from_phone = msg.get("from")
            msg_type = msg.get("type") or ""
            if not msg_id or not from_phone:
                continue

            text: str | None = None
            media_id: str | None = None
            media_mime: str | None = None
            button_payload: str | None = None
            button_text: str | None = None

            if msg_type == "text":
                text = (msg.get("text") or {}).get("body")
            elif msg_type == "audio":
                audio = msg.get("audio") or {}
                media_id = audio.get("id")
                media_mime = audio.get("mime_type")
            elif msg_type == "voice":
                # Some Meta payloads use the "voice" type for push-to-talk
                # voice notes — treat identically to "audio".
                voice = msg.get("voice") or {}
                media_id = voice.get("id")
                media_mime = voice.get("mime_type")
            elif msg_type == "button":
                # A tap on a message-template's Quick Reply button — Meta
                # echoes the button's configured payload plus its visible
                # text. Also fed to the agent as plain text (button_text)
                # so a non-Stop quick-reply still gets a sensible reply.
                button = msg.get("button") or {}
                button_payload = button.get("payload")
                button_text = button.get("text")
                text = button_text
            elif msg_type == "interactive":
                # A tap on a free-form interactive (non-template) button,
                # e.g. sent via wa_client's own interactive-message path.
                interactive = msg.get("interactive") or {}
                button_reply = interactive.get("button_reply") or {}
                button_payload = button_reply.get("id")
                button_text = button_reply.get("title")
                text = button_text

            return InboundMessage(
                id=str(msg_id),
                from_phone=_normalize_phone(str(from_phone)),
                type=msg_type,
                text=text,
                media_id=media_id,
                media_mime=media_mime,
                button_payload=button_payload,
                button_text=button_text,
            )
    return None


def _extract_phone_number_id(payload: dict[str, Any]) -> str | None:
    """The WABA number (Meta's `metadata.phone_number_id`) this message
    arrived on — present on every inbound envelope alongside `messages[]`,
    used by `_resolve_org_id` to attribute the message to the right org."""
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            phone_number_id = ((change.get("value") or {}).get("metadata") or {}).get(
                "phone_number_id"
            )
            if phone_number_id:
                return str(phone_number_id)
    return None


async def _resolve_org_id(db: AsyncSession, phone_number_id: str | None) -> UUID:
    """The org that owns this WABA number, else the platform default.

    An org can have several dedicated WhatsApp numbers (see
    db/models/org_phone_number.py) — any of them resolves to that org, not
    just its "default" one. A number with no matching org (not yet
    provisioned, or Meta test number on the platform's own default WABA) is
    expected during onboarding — logs rather than raises so the message
    still gets a reply.

    Since the same WhatsApp number is now allowed to be registered under
    more than one org (see db/models/org_phone_number.py's partial unique
    index), more than one row can match here — genuinely ambiguous, since
    Meta gives no other signal to disambiguate. Deterministically picks
    whichever org registered it first (``created_at``) rather than crashing
    (``.scalar_one_or_none()`` raises ``MultipleResultsFound``), and warns so
    it's visible this number needs to be un-shared.
    """
    if phone_number_id:
        stmt = (
            select(OrgPhoneNumber.org_id)
            .where(
                OrgPhoneNumber.provider == "whatsapp",
                OrgPhoneNumber.phone_number == phone_number_id,
            )
            .order_by(OrgPhoneNumber.created_at)
            .limit(2)
        )
        org_ids = (await db.execute(stmt)).scalars().all()
        if len(org_ids) > 1:
            logger.warning(
                "whatsapp_number_ambiguous_org",
                phone_number_id=phone_number_id,
                resolved_org_id=str(org_ids[0]),
                other_org_ids=[str(o) for o in org_ids[1:]],
            )
        if org_ids:
            return org_ids[0]
        logger.info("whatsapp_org_lookup_miss", phone_number_id=phone_number_id)
    return UUID(settings.default_org_id)


# Keep strong references to fire-and-forget tasks so the event loop doesn't
# garbage-collect them mid-flight.
_pending_tasks: set[asyncio.Task[Any]] = set()


def _fire_and_forget(coro: Any) -> None:
    """Run a best-effort coroutine without blocking the caller."""
    task = asyncio.create_task(coro)
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


# Rolling window of per-turn stage timings, readable via GET /diag/latency.
# Temporary, tied to the latency investigation — delete with the diag probe.
_TIMINGS_KEY = "veerox:diag:wa_timings"
_TIMINGS_KEEP = 10


async def _record_turn_timings(timings: dict[str, Any]) -> None:
    """Best-effort push of one turn's timings onto a capped Redis list."""
    try:
        redis = get_redis_pool()
        await redis.lpush(_TIMINGS_KEY, json.dumps(timings))  # type: ignore[misc]
        await redis.ltrim(_TIMINGS_KEY, 0, _TIMINGS_KEEP - 1)  # type: ignore[misc]
    except Exception:
        logger.warning("wa_timings_record_failed", exc_info=True)


async def _claim_message_id(msg_id: str) -> bool:
    """Redis SETNX with 24h TTL — first claim wins, retries return False."""
    redis = get_redis_pool()
    key = f"{_IDEMPOTENCY_KEY_PREFIX}{msg_id}"
    acquired = await redis.set(key, "1", nx=True, ex=_IDEMPOTENCY_TTL_SECS)
    return bool(acquired)


async def _find_open_campaign_target(db: AsyncSession, phone: str) -> UUID | None:
    """Find this phone's still-open WhatsApp campaign outreach, if any.

    "Open" means the dispatcher (workers/whatsapp_dispatcher.py) already sent
    the opening message (status="completed") and the AI hasn't resolved it
    yet (qualified IS NULL) — matches on phone only, not conversation_id, so
    every turn of the reply thread keeps resolving campaign_target_id, not
    just the first one (mirrors the intent of voice's per-call
    campaign_target_id, adapted for WhatsApp's multi-turn webhook model).
    """
    stmt = (
        select(CampaignTarget.id)
        .where(
            CampaignTarget.phone == phone,
            CampaignTarget.channel == "whatsapp",
            CampaignTarget.status == "completed",
            CampaignTarget.qualified.is_(None),
        )
        .order_by(CampaignTarget.called_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _get_or_create_user(db: AsyncSession, org_id: UUID, phone: str) -> User:
    """Resolve or create a User row keyed by ``(org_id, phone)``.

    Logic mirrors ``apps.api.core.tools._get_or_create_user_by_phone`` — we
    deliberately don't import it so the channel layer stays self-contained.
    """
    stmt = select(User).where(User.org_id == org_id, User.phone == phone)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    user = User(org_id=org_id, phone=phone)
    db.add(user)
    await db.flush()
    return user


async def _resolve_text(
    msg: InboundMessage, *, api_key: str | None = None, meta_access_token: str | None = None
) -> str:
    """Convert an inbound message to plain text the agent can reason over."""
    if msg.type == "text":
        return (msg.text or "").strip() or _UNSUPPORTED_PLACEHOLDER

    if msg.type in {"audio", "voice"} and msg.media_id and meta_access_token:
        audio_bytes = await wa_client.download_media(meta_access_token, msg.media_id)
        mime = msg.media_mime or "audio/ogg"
        transcript = await transcribe(audio_bytes, mime=mime, api_key=api_key)
        # Whisper occasionally returns an empty string for very short
        # blobs; let the agent see the placeholder so it can recover.
        return transcript or _UNSUPPORTED_PLACEHOLDER

    return _UNSUPPORTED_PLACEHOLDER


async def process_inbound(payload: dict[str, Any]) -> None:
    """Background-task entrypoint — never raises, always logs."""
    try:
        msg = _extract_message(payload)
        if msg is None:
            logger.info("whatsapp_inbound_no_message", reason="status_or_empty")
            return

        # Idempotency comes before any side effect (DB write, OpenAI spend).
        # If Meta retries, we no-op silently.
        if not await _claim_message_id(msg.id):
            logger.info("whatsapp_inbound_duplicate_skipped", wa_message_id=msg.id)
            return

        phone_number_id = _extract_phone_number_id(payload)

        started = time.monotonic()
        async with AsyncSessionLocal() as db:
            org_id = await _resolve_org_id(db, phone_number_id)
            org_record = await db.get(Org, org_id)
            meta_creds = resolve_meta_credentials(org_record)

            # Read receipt + typing indicator, off the critical path — the
            # user sees "typing…" while the agent works. mark_read swallows
            # its own errors, so fire-and-forget is safe. Sent from the same
            # number the message arrived on (this org's own dedicated
            # number). Skipped outright when this org has no Meta
            # credentials configured — there's nothing to send it with.
            if meta_creds is not None:
                _fire_and_forget(
                    wa_client.mark_read(
                        meta_creds.access_token,
                        msg.id,
                        typing=True,
                        phone_number_id=phone_number_id,
                    )
                )

            user = await _get_or_create_user(db, org_id, msg.from_phone)

            # A tap on the follow-up template's "Stop" quick-reply button is
            # handled deterministically here — no LLM call, no risk of the
            # agent misreading intent — same effect as the AI's own
            # mark_not_interested tool call or a manual "Not Interested" in
            # the dashboard (see routers/admin.py's update_lead).
            if msg.type in {"button", "interactive"} and _is_stop_button_tap(msg):
                result = await mark_not_interested(
                    db, reason="tapped_stop_button", user_id=user.id, org_id=org_id
                )
                logger.info("whatsapp_stop_button_tapped", from_phone=msg.from_phone, result=result)
                if meta_creds is not None:
                    await wa_client.send_text(
                        meta_creds.access_token,
                        msg.from_phone,
                        "Got it — we won't send any more follow-ups. Reach out anytime if you change your mind.",
                        phone_number_id=phone_number_id,
                    )
                return

            campaign_target_id = await _find_open_campaign_target(db, msg.from_phone)
            # Commit the user row before the (potentially long) LLM call so a
            # later failure doesn't lose the contact record.
            await db.commit()
            db_done = time.monotonic()

            text = await _resolve_text(
                msg,
                api_key=resolve_openai_api_key(org_record),
                meta_access_token=meta_creds.access_token if meta_creds else None,
            )
            resolve_done = time.monotonic()

            reply = await agent_core.handle_turn(
                db,
                user_id=user.id,
                channel="whatsapp",
                input_text=text,
                campaign_target_id=campaign_target_id,
                org_id=org_id,
                whatsapp_phone_number_id=phone_number_id,
            )
            agent_done = time.monotonic()

        # send_text can raise — we let it bubble into the outer except so
        # structlog and Sentry capture the failure. (The read receipt was
        # already fired above, before the agent ran.) Replies go out from
        # the same number the message came in on.
        #
        # Skip it when this turn booked an appointment: book_appointment
        # (core/tools.py) already sent its own pre-approved template
        # confirmation, so sending the model's own text reply too would
        # double-confirm the same booking with two back-to-back messages.
        if not appointment_booked_this_turn():
            if meta_creds is None:
                logger.warning(
                    "whatsapp_reply_skipped_not_configured",
                    org_id=str(org_id),
                    wa_message_id=msg.id,
                )
            else:
                await wa_client.send_text(
                    meta_creds.access_token, msg.from_phone, reply, phone_number_id=phone_number_id
                )
        send_done = time.monotonic()

        timings = {
            "wa_message_id": msg.id,
            "type": msg.type,
            "reply_chars": len(reply),
            "db_ms": int((db_done - started) * 1000),
            "resolve_ms": int((resolve_done - db_done) * 1000),
            "agent_ms": int((agent_done - resolve_done) * 1000),
            "send_ms": int((send_done - agent_done) * 1000),
            "total_ms": int((send_done - started) * 1000),
        }
        logger.info("whatsapp_inbound_processed", from_phone=msg.from_phone, **timings)
        await _record_turn_timings(timings)
    except Exception as exc:
        # Catch-all so a single bad message can't kill the background loop.
        # Sentry is wired via structlog -> sentry_sdk in apps.api.sentry.
        logger.exception(
            "whatsapp_inbound_failed",
            error=str(exc),
            payload_object=payload.get("object"),
        )
        await record_error()
