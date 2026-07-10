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
from apps.api.core.agent import agent_core
from apps.api.core.transcribe import transcribe
from apps.api.db.models.user import User
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import get_redis_pool

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

            return InboundMessage(
                id=str(msg_id),
                from_phone=_normalize_phone(str(from_phone)),
                type=msg_type,
                text=text,
                media_id=media_id,
                media_mime=media_mime,
            )
    return None


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


async def _resolve_text(msg: InboundMessage) -> str:
    """Convert an inbound message to plain text the agent can reason over."""
    if msg.type == "text":
        return (msg.text or "").strip() or _UNSUPPORTED_PLACEHOLDER

    if msg.type in {"audio", "voice"} and msg.media_id:
        audio_bytes = await wa_client.download_media(msg.media_id)
        mime = msg.media_mime or "audio/ogg"
        transcript = await transcribe(audio_bytes, mime=mime)
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

        # Read receipt + typing indicator straight away, off the critical
        # path — the user sees "typing…" while the agent works. mark_read
        # swallows its own errors, so fire-and-forget is safe.
        _fire_and_forget(wa_client.mark_read(msg.id, typing=True))

        org_id = UUID(settings.default_org_id)

        started = time.monotonic()
        async with AsyncSessionLocal() as db:
            user = await _get_or_create_user(db, org_id, msg.from_phone)
            # Commit the user row before the (potentially long) LLM call so a
            # later failure doesn't lose the contact record.
            await db.commit()
            db_done = time.monotonic()

            text = await _resolve_text(msg)
            resolve_done = time.monotonic()

            reply = await agent_core.handle_turn(
                db,
                user_id=user.id,
                channel="whatsapp",
                input_text=text,
            )
            agent_done = time.monotonic()

        # send_text can raise — we let it bubble into the outer except so
        # structlog and Sentry capture the failure. (The read receipt was
        # already fired above, before the agent ran.)
        await wa_client.send_text(msg.from_phone, reply)
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
