"""Rollout metrics for the Conversation Engine (quotation 2.4).

Computed from the turn log, so nothing extra is recorded on the call path:

* scenario accuracy  — share of *reviewed* turns marked correct (Shadow Mode
  review screen); reported with how many turns have been reviewed.
* latency            — mean / p50 / p95 of the engine's own time per turn.
* fallback rate      — turns the engine handed back to the script because of a
  problem. Smart Script hand-offs are by design and counted separately.
* call completion    — conversations that reached a finished state
  (callback set, appointment set, info sent, closed or handed to a human).
* appointment conversion, transfer rate — conversations with a successful
  ``book_appointment`` / ``transfer_to_human`` action.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.conversation.types import (
    STATE_APPOINTMENT_SET,
    STATE_CALLBACK_SET,
    STATE_CLOSED,
    STATE_HANDED_OFF,
    STATE_INFO_SENT,
)
from apps.api.db.models.conversation_engine import ConversationTurn

_FINISHED_STATES = frozenset(
    {STATE_CALLBACK_SET, STATE_APPOINTMENT_SET, STATE_INFO_SENT, STATE_CLOSED, STATE_HANDED_OFF}
)
_MAX_ROWS = 50_000
# Fallback reasons that are by design, not a problem.
_BY_DESIGN = frozenset({"smart_script"})


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


async def compute_metrics(
    db: AsyncSession, org_id: UUID, *, days: int = 7, channel: str | None = None
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(
            ConversationTurn.conversation_id,
            ConversationTurn.channel,
            ConversationTurn.mode,
            ConversationTurn.scenario,
            ConversationTurn.state,
            ConversationTurn.action_id,
            ConversationTurn.action_result,
            ConversationTurn.fallback_reason,
            ConversationTurn.latency_ms,
            ConversationTurn.review_label,
            ConversationTurn.created_at,
        )
        .where(ConversationTurn.org_id == org_id, ConversationTurn.created_at >= since)
        .order_by(ConversationTurn.created_at.asc())
        .limit(_MAX_ROWS)
    )
    if channel is not None:
        stmt = stmt.where(ConversationTurn.channel == channel)
    rows = (await db.execute(stmt)).all()

    total = len(rows)
    problems = [r for r in rows if r.fallback_reason and r.fallback_reason not in _BY_DESIGN]
    by_design = [r for r in rows if r.fallback_reason in _BY_DESIGN]
    answered = total - len(problems) - len(by_design)
    latencies = [r.latency_ms for r in rows if r.latency_ms is not None]

    reviewed = [r for r in rows if r.review_label in ("correct", "incorrect")]
    correct = sum(1 for r in reviewed if r.review_label == "correct")

    # Per conversation: the last state the engine actually reached, and which
    # outcome actions succeeded.
    last_state: dict[Any, str | None] = {}
    booked: set[Any] = set()
    transferred: set[Any] = set()
    for r in rows:
        if r.conversation_id is None:
            continue
        if r.fallback_reason is None and r.state:
            last_state[r.conversation_id] = r.state
        else:
            last_state.setdefault(r.conversation_id, None)
        succeeded = r.action_result is not None and r.action_result.get("status") != "error"
        if r.action_id == "book_appointment" and succeeded:
            booked.add(r.conversation_id)
        if r.action_id == "transfer_to_human" and succeeded:
            transferred.add(r.conversation_id)
    conversations = len(last_state)
    completed = sum(1 for state in last_state.values() if state in _FINISHED_STATES)

    return {
        "days": days,
        "channel": channel,
        "turns": total,
        "engine_answered": answered,
        "handed_to_script_by_design": len(by_design),
        "fallbacks": len(problems),
        "fallback_rate": _ratio(len(problems), total),
        "fallback_reasons": dict(Counter(r.fallback_reason for r in problems)),
        "latency_ms": {
            "mean": round(sum(latencies) / len(latencies)) if latencies else None,
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
        },
        "scenarios": dict(Counter(r.scenario for r in rows if r.scenario).most_common()),
        "reviewed_turns": len(reviewed),
        "scenario_accuracy": _ratio(correct, len(reviewed)),
        "conversations": conversations,
        "call_completion_rate": _ratio(completed, conversations),
        "appointments_booked": len(booked),
        "appointment_conversion": _ratio(len(booked), conversations),
        "transfers": len(transferred),
        "transfer_rate": _ratio(len(transferred), conversations),
        "by_channel": dict(Counter(r.channel for r in rows)),
        "by_mode": dict(Counter(r.mode for r in rows)),
    }
