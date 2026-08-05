"""Monthly usage aggregation for plan-limit enforcement and the billing
dashboard's usage bars.

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


def current_month_start() -> datetime:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class MonthlyUsage:
    period_start: datetime
    call_minutes: float
    whatsapp_messages: float


async def get_monthly_usage(db: AsyncSession, org_id: UUID) -> MonthlyUsage:
    period_start = current_month_start()

    # A plan upgrade mid-month shouldn't be immediately eaten by usage
    # already accrued under the previous plan this month — count from
    # whichever is later: the calendar month's start, or when the org's
    # current plan took effect (Org.plan_started_at, set on checkout/
    # payment activation in routers/billing.py).
    org_result = await db.execute(select(Org.plan_started_at).where(Org.id == org_id))
    plan_started_at = org_result.scalar_one_or_none()
    if plan_started_at is not None and plan_started_at > period_start:
        period_start = plan_started_at

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
    voice_conversations_result = await db.execute(
        select(Conversation.started_at, Conversation.ended_at, Conversation.recording_duration_secs).where(
            Conversation.org_id == org_id,
            Conversation.channel == "voice",
            Conversation.started_at >= period_start,
        )
    )
    call_seconds = 0.0
    for started_at, ended_at, recording_duration_secs in voice_conversations_result.all():
        if ended_at is not None:
            call_seconds += (ended_at - started_at).total_seconds()
        elif recording_duration_secs is not None:
            call_seconds += recording_duration_secs

    whatsapp_count_result = await db.execute(
        select(func.count()).select_from(Message).where(
            Message.org_id == org_id,
            Message.channel == "whatsapp",
            Message.created_at >= period_start,
        )
    )
    whatsapp_count = whatsapp_count_result.scalar_one()

    return MonthlyUsage(
        period_start=period_start,
        call_minutes=float(call_seconds) / 60.0,
        whatsapp_messages=float(whatsapp_count),
    )
