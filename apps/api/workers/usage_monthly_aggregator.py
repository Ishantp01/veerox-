"""Background worker that rolls up `UsageDaily` rows into `UsageMonthly`.

Runs as an in-process `asyncio.create_task` from the FastAPI lifespan (see
`apps/api/main.py`), mirroring `workers/license_expiry_worker.py`'s
poll-loop structure.

Idempotent: recomputes the current (open) billing period's totals fresh
from `UsageDaily` every tick and upserts on `UsageMonthly`'s
`(org_id, billing_period, service, usage_type)` unique key. A period whose
`calculation_status == "locked"` (closed by workers/billing_worker.py) is
never touched again here — a late-arriving event after lock becomes a
`UsageAdjustment` instead (see routers for the period-close flow).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.usage_daily import UsageDaily
from apps.api.db.models.usage_monthly import UsageMonthly
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECS = 24 * 3600


def current_billing_period(today: date | None = None) -> str:
    """"YYYY-MM" for the given (or current UTC) date — the canonical
    billing-period key used across UsageMonthly/CostAllocation/Invoice."""
    d = today or datetime.now(UTC).date()
    return f"{d.year:04d}-{d.month:02d}"


async def aggregate_monthly(db: AsyncSession, billing_period: str) -> int:
    """Recompute every org/service/usage_type rollup for `billing_period`
    from `UsageDaily` and upsert into `UsageMonthly`. Skips (and logs) any
    row already locked. Returns the number of rows written."""
    year, month = (int(p) for p in billing_period.split("-"))
    month_start = date(year, month, 1)
    month_end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    stmt = (
        select(
            UsageDaily.org_id,
            UsageDaily.service,
            UsageDaily.usage_type,
            func.sum(UsageDaily.total_quantity).label("total_quantity"),
            func.sum(UsageDaily.estimated_cost).label("third_party_cost"),
        )
        .where(UsageDaily.usage_date >= month_start, UsageDaily.usage_date < month_end)
        .group_by(UsageDaily.org_id, UsageDaily.service, UsageDaily.usage_type)
    )
    rows = (await db.execute(stmt)).all()

    written = 0
    skipped_locked = 0
    for row in rows:
        existing = (
            await db.execute(
                select(UsageMonthly).where(
                    UsageMonthly.org_id == row.org_id,
                    UsageMonthly.billing_period == billing_period,
                    UsageMonthly.service == row.service,
                    UsageMonthly.usage_type == row.usage_type,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            if existing.calculation_status == "locked":
                skipped_locked += 1
                continue
            existing.total_quantity = row.total_quantity
            existing.third_party_cost = row.third_party_cost
            existing.calculation_status = "calculated"
        else:
            try:
                async with db.begin_nested():
                    db.add(
                        UsageMonthly(
                            org_id=row.org_id,
                            billing_period=billing_period,
                            service=row.service,
                            usage_type=row.usage_type,
                            total_quantity=row.total_quantity,
                            third_party_cost=row.third_party_cost,
                            calculation_status="calculated",
                        )
                    )
                    await db.flush()
            except IntegrityError:
                fetched = (
                    await db.execute(
                        select(UsageMonthly).where(
                            UsageMonthly.org_id == row.org_id,
                            UsageMonthly.billing_period == billing_period,
                            UsageMonthly.service == row.service,
                            UsageMonthly.usage_type == row.usage_type,
                        )
                    )
                ).scalar_one()
                if fetched.calculation_status != "locked":
                    fetched.total_quantity = row.total_quantity
                    fetched.third_party_cost = row.third_party_cost
                    fetched.calculation_status = "calculated"
        written += 1

    if skipped_locked:
        logger.info(
            "usage_monthly_aggregator_skipped_locked", count=skipped_locked, period=billing_period
        )
    return written


async def run_monthly_aggregation_once(billing_period: str | None = None) -> int:
    period = billing_period or current_billing_period()
    async with AsyncSessionLocal() as db:
        written = await aggregate_monthly(db, period)
        await db.commit()
    return written


async def run_usage_monthly_aggregator() -> None:
    """The worker's main loop — runs for the lifetime of the app process."""
    while True:
        try:
            count = await run_monthly_aggregation_once()
            logger.info("usage_monthly_aggregator_tick", rows_written=count)
        except Exception:  # noqa: BLE001
            logger.exception("usage_monthly_aggregator_tick_failed")
            await record_error()
        await asyncio.sleep(_POLL_INTERVAL_SECS)
