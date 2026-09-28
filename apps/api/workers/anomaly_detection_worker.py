"""Flags organizations whose usage on a given day jumped well past their own
recent baseline (req §13's "usage anomalies" for the owner dashboard).

Deliberately read-only/on-demand rather than a persisted table: anomalies
are a lens over `UsageDaily` computed at request time
(`routers/usage.py`'s admin anomalies endpoint calls `detect_anomalies`
directly), so there's no separate ledger to keep in sync or lock alongside
billing periods. If persistence is ever needed (e.g. to track
acknowledgement), it can reuse `UsageAdjustment`-style storage later without
this function's signature changing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.usage_daily import UsageDaily

# An org/service/usage_type day is flagged when it exceeds this multiple of
# its own trailing 7-day average — simple and documented, not a statistical
# model; tune once real usage data exists.
_ANOMALY_MULTIPLIER = Decimal("3")
_BASELINE_DAYS = 7


@dataclass(frozen=True)
class UsageAnomaly:
    org_id: UUID
    service: str
    usage_type: str
    usage_date: date
    quantity: Decimal
    baseline_avg: Decimal


async def detect_anomalies(db: AsyncSession, as_of: date) -> list[UsageAnomaly]:
    baseline_start = as_of - timedelta(days=_BASELINE_DAYS)

    today_rows = (
        await db.execute(select(UsageDaily).where(UsageDaily.usage_date == as_of))
    ).scalars().all()

    anomalies: list[UsageAnomaly] = []
    for row in today_rows:
        baseline_rows = (
            await db.execute(
                select(UsageDaily.total_quantity).where(
                    UsageDaily.org_id == row.org_id,
                    UsageDaily.service == row.service,
                    UsageDaily.usage_type == row.usage_type,
                    UsageDaily.usage_date >= baseline_start,
                    UsageDaily.usage_date < as_of,
                )
            )
        ).scalars().all()
        if not baseline_rows:
            continue
        avg = sum(baseline_rows, Decimal("0")) / len(baseline_rows)
        if avg > 0 and row.total_quantity > avg * _ANOMALY_MULTIPLIER:
            anomalies.append(
                UsageAnomaly(
                    org_id=row.org_id,
                    service=row.service,
                    usage_type=row.usage_type,
                    usage_date=row.usage_date,
                    quantity=row.total_quantity,
                    baseline_avg=avg,
                )
            )
    return anomalies
