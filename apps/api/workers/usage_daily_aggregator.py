"""Background worker that rolls up `UsageEvent` rows into `UsageDaily`.

Runs as an in-process `asyncio.create_task` from the FastAPI lifespan (see
`apps/api/main.py`), mirroring `workers/license_expiry_worker.py`'s
poll-loop structure.

Idempotent by design: each tick upserts on `UsageDaily`'s
`(org_id, usage_date, service, usage_type)` unique key, computing the day's
totals fresh from `UsageEvent` every time rather than incrementing a
running counter — so re-running the same day (including "today", which is
always partial) never double-counts, and a crash mid-tick just means the
next tick recomputes the same numbers.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.cost_rate import CostRate
from apps.api.db.models.usage_daily import UsageDaily
from apps.api.db.models.usage_event import UsageEvent
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECS = 3600
# How many trailing days (including today) get recomputed each tick — covers
# events that landed slightly late for "yesterday" without rescanning the
# whole ledger on every run.
_LOOKBACK_DAYS = 2


async def _unit_cost(
    db: AsyncSession, service: str, usage_type: str, provider: str | None, at: datetime
) -> Decimal | None:
    """Best matching `CostRate` for this row — a provider-specific rate
    wins over a provider-agnostic one when both are in effect."""
    stmt = (
        select(CostRate.unit_cost, CostRate.provider)
        .where(
            CostRate.service == service,
            CostRate.usage_type == usage_type,
            CostRate.effective_from <= at,
        )
        .where((CostRate.effective_to.is_(None)) | (CostRate.effective_to > at))
        .order_by(CostRate.effective_from.desc())
    )
    rows = (await db.execute(stmt)).all()
    if not rows:
        return None
    provider_specific = [r for r in rows if r.provider == provider and provider is not None]
    generic = [r for r in rows if r.provider is None]
    if provider_specific:
        return Decimal(provider_specific[0].unit_cost)
    if generic:
        return Decimal(generic[0].unit_cost)
    return None


async def _aggregate_day(db: AsyncSession, usage_date: date) -> int:
    """Recompute every org/service/usage_type rollup for one UTC day and
    upsert it into `UsageDaily`. Returns the number of rows written."""
    day_start = datetime(usage_date.year, usage_date.month, usage_date.day, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)

    stmt = (
        select(
            UsageEvent.org_id,
            UsageEvent.service,
            UsageEvent.usage_type,
            UsageEvent.provider,
            func.sum(UsageEvent.quantity).label("total_quantity"),
            func.count(UsageEvent.id).label("event_count"),
        )
        .where(UsageEvent.created_at >= day_start, UsageEvent.created_at < day_end)
        .group_by(UsageEvent.org_id, UsageEvent.service, UsageEvent.usage_type, UsageEvent.provider)
    )
    rows = (await db.execute(stmt)).all()

    # Collapse the per-provider groupby above into one row per
    # (org, service, usage_type) — UsageDaily doesn't split by provider —
    # while still using each group's own provider to look up its cost rate.
    collapsed: dict[tuple[UUID, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.org_id, row.service, row.usage_type)
        cost = await _unit_cost(db, row.service, row.usage_type, row.provider, day_start)
        estimated_cost = (cost * row.total_quantity) if cost is not None else None
        bucket = collapsed.setdefault(
            key, {"total_quantity": Decimal("0"), "event_count": 0, "estimated_cost": None}
        )
        bucket["total_quantity"] += row.total_quantity
        bucket["event_count"] += row.event_count
        if estimated_cost is not None:
            bucket["estimated_cost"] = (bucket["estimated_cost"] or Decimal("0")) + estimated_cost

    written = 0
    for (org_id, service, usage_type), agg in collapsed.items():
        # Portable upsert (works on both Postgres prod and the SQLite test
        # DB, unlike a dialect-specific ON CONFLICT): look the row up first;
        # if a concurrent tick/instance beat us to the insert, the
        # unique-constraint violation is caught via a SAVEPOINT (same
        # pattern as workers/follow_up_dispatcher.py) and we just update the
        # now-existing row instead.
        existing = (
            await db.execute(
                select(UsageDaily).where(
                    UsageDaily.org_id == org_id,
                    UsageDaily.usage_date == usage_date,
                    UsageDaily.service == service,
                    UsageDaily.usage_type == usage_type,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.total_quantity = agg["total_quantity"]
            existing.event_count = agg["event_count"]
            existing.estimated_cost = agg["estimated_cost"]
        else:
            try:
                async with db.begin_nested():
                    db.add(
                        UsageDaily(
                            org_id=org_id,
                            usage_date=usage_date,
                            service=service,
                            usage_type=usage_type,
                            total_quantity=agg["total_quantity"],
                            event_count=agg["event_count"],
                            estimated_cost=agg["estimated_cost"],
                        )
                    )
                    await db.flush()
            except IntegrityError:
                conflicting = (
                    await db.execute(
                        select(UsageDaily).where(
                            UsageDaily.org_id == org_id,
                            UsageDaily.usage_date == usage_date,
                            UsageDaily.service == service,
                            UsageDaily.usage_type == usage_type,
                        )
                    )
                ).scalar_one()
                conflicting.total_quantity = agg["total_quantity"]
                conflicting.event_count = agg["event_count"]
                conflicting.estimated_cost = agg["estimated_cost"]
        written += 1
    return written


async def run_daily_aggregation_once() -> int:
    """Recompute `UsageDaily` for the last `_LOOKBACK_DAYS` days (including
    today). Exposed separately from the poll loop so tests and the admin
    period-close endpoint can trigger it synchronously."""
    total = 0
    today = datetime.now(UTC).date()
    async with AsyncSessionLocal() as db:
        for offset in range(_LOOKBACK_DAYS, -1, -1):
            total += await _aggregate_day(db, today - timedelta(days=offset))
        await db.commit()
    return total


async def run_usage_daily_aggregator() -> None:
    """The worker's main loop — runs for the lifetime of the app process."""
    while True:
        try:
            count = await run_daily_aggregation_once()
            logger.info("usage_daily_aggregator_tick", rows_written=count)
        except Exception:  # noqa: BLE001
            logger.exception("usage_daily_aggregator_tick_failed")
            await record_error()
        await asyncio.sleep(_POLL_INTERVAL_SECS)
