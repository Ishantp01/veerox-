"""Credit usage aggregation for plan-limit enforcement and the billing
dashboard's usage bars.

Recharge-based, not calendar-based: an org's included credits are consumed
until they run out, and the only thing that restores them is buying a plan
again (checkout sets `Org.plan_started_at`, which is where this counts
from). Nothing resets on the 1st of the month — see routers/billing.py.

Computed live from `messages`/`conversations` rather than a maintained
counter table, so this reuses data already recorded per turn instead of
adding new increment call sites that could drift out of sync with what
actually happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.conversation import Conversation
from apps.api.db.models.message import Message
from apps.api.db.models.org import Org


@dataclass(frozen=True)
class CreditUsage:
    """`period_start` is when the org's current credits were granted — i.e.
    its last recharge — not a calendar boundary."""

    period_start: datetime
    call_minutes: float
    whatsapp_messages: float


async def get_credit_usage(db: AsyncSession, org_id: UUID) -> CreditUsage:
    # Credits are granted per recharge, so usage counts from the moment the
    # org's current plan took effect (Org.plan_started_at, set on checkout/
    # payment activation in routers/billing.py) and keeps accumulating until
    # the next one.
    #
    # An org with no recharge on record (assigned a plan directly by an
    # admin, or a pre-backfill row) gets *no* lower bound at all — every
    # message and call it has ever made counts. That's the conservative
    # reading: pretending it started fresh would hand out free credits. Note
    # this is deliberately an absent filter rather than a very old
    # timestamp, so there's no boundary for a row to land exactly on.
    org_result = await db.execute(
        select(Org.plan_started_at, Org.created_at).where(Org.id == org_id)
    )
    row = org_result.one_or_none()
    if row is None:
        return CreditUsage(period_start=datetime.now(UTC), call_minutes=0.0, whatsapp_messages=0.0)
    plan_started_at, created_at = row
    # Reported for display only; the filters below key off plan_started_at.
    period_start = plan_started_at or created_at

    # Message.audio_secs is never populated for the realtime voice path (the
    # Realtime API doesn't report per-turn audio duration), so duration comes
    # from Conversation instead. Prefer ended_at - started_at: it's set
    # in-process by close_voice_conversation the moment the realtime bridge
    # disconnects, with no dependency on an externally-reachable webhook.
    # Fall back to Plivo's recording-finished callback (recording_duration_secs)
    # only for the rare case the bridge never closed the conversation (crash) —
    # that callback requires PUBLIC_BASE_URL to be reachable from Plivo, which
    # local/ngrok setups don't always guarantee. Summed in Python rather than
    # SQL since a portable started_at/ended_at subtraction doesn't compile the
    # same way across SQLite (tests) and Postgres (prod).
    voice_filters = [Conversation.org_id == org_id, Conversation.channel == "voice"]
    if plan_started_at is not None:
        voice_filters.append(Conversation.started_at >= plan_started_at)
    voice_conversations_result = await db.execute(
        select(Conversation.started_at, Conversation.ended_at, Conversation.recording_duration_secs).where(
            *voice_filters
        )
    )
    call_seconds = 0.0
    for started_at, ended_at, recording_duration_secs in voice_conversations_result.all():
        if ended_at is not None:
            call_seconds += (ended_at - started_at).total_seconds()
        elif recording_duration_secs is not None:
            call_seconds += recording_duration_secs

    whatsapp_filters = [Message.org_id == org_id, Message.channel == "whatsapp"]
    if plan_started_at is not None:
        whatsapp_filters.append(Message.created_at >= plan_started_at)
    whatsapp_count_result = await db.execute(
        select(func.count()).select_from(Message).where(*whatsapp_filters)
    )
    whatsapp_count = whatsapp_count_result.scalar_one()

    return CreditUsage(
        period_start=period_start,
        call_minutes=float(call_seconds) / 60.0,
        whatsapp_messages=float(whatsapp_count),
    )
