"""Covers workers/usage_daily_aggregator.py and
workers/usage_monthly_aggregator.py: idempotency (running twice never
double-counts) and that a locked UsageMonthly row is left untouched."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.core.usage import deterministic_event_id, record_usage
from apps.api.db.models import Org
from apps.api.db.models.usage_daily import UsageDaily
from apps.api.db.models.usage_monthly import UsageMonthly


@pytest.fixture(autouse=True)
def _redirect_worker_sessions(test_engine, monkeypatch: pytest.MonkeyPatch):
    """These workers open their own `AsyncSessionLocal()` (background-job
    pattern, not a request-scoped dependency) — redirect to the same
    in-memory test engine `db_session` uses, matching
    test_org_license.py's `test_license_expiry_worker_expires_due_orgs`."""
    from apps.api.workers import usage_daily_aggregator, usage_monthly_aggregator

    test_session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(usage_daily_aggregator, "AsyncSessionLocal", test_session_factory)
    monkeypatch.setattr(usage_monthly_aggregator, "AsyncSessionLocal", test_session_factory)


async def test_daily_aggregation_is_idempotent(db_session: AsyncSession) -> None:
    from apps.api.workers.usage_daily_aggregator import run_daily_aggregation_once

    org = Org(name="Aggregated Co")
    db_session.add(org)
    await db_session.flush()

    for i in range(3):
        await record_usage(
            db_session,
            organization_id=org.id,
            event_id=deterministic_event_id("test_agg", str(i)),
            service="voice",
            usage_type="voice_seconds",
            quantity=10,
            unit="seconds",
            source="test",
        )
    await db_session.commit()

    await run_daily_aggregation_once()
    await run_daily_aggregation_once()  # second run must not double the totals

    rows = (
        await db_session.execute(select(UsageDaily).where(UsageDaily.org_id == org.id))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].total_quantity == 30
    assert rows[0].event_count == 3


async def test_monthly_aggregation_skips_locked_period(db_session: AsyncSession) -> None:
    from apps.api.workers.usage_monthly_aggregator import aggregate_monthly, current_billing_period

    org = Org(name="Locked Co")
    db_session.add(org)
    await db_session.flush()

    period = current_billing_period()
    today = datetime.now(UTC).date()
    db_session.add(
        UsageDaily(
            org_id=org.id,
            usage_date=today,
            service="voice",
            usage_type="voice_seconds",
            total_quantity=50,
            event_count=1,
        )
    )
    db_session.add(
        UsageMonthly(
            org_id=org.id,
            billing_period=period,
            service="voice",
            usage_type="voice_seconds",
            total_quantity=999,  # a value the aggregator must NOT overwrite
            calculation_status="locked",
        )
    )
    await db_session.commit()

    await aggregate_monthly(db_session, period)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(UsageMonthly).where(
                UsageMonthly.org_id == org.id, UsageMonthly.billing_period == period
            )
        )
    ).scalar_one()
    assert row.total_quantity == 999
    assert row.calculation_status == "locked"


async def test_monthly_aggregation_updates_an_open_period(db_session: AsyncSession) -> None:
    from apps.api.workers.usage_monthly_aggregator import aggregate_monthly, current_billing_period

    org = Org(name="Open Co")
    db_session.add(org)
    await db_session.flush()

    period = current_billing_period()
    today = datetime.now(UTC).date()
    db_session.add(
        UsageDaily(
            org_id=org.id,
            usage_date=today,
            service="voice",
            usage_type="voice_seconds",
            total_quantity=50,
            event_count=1,
        )
    )
    await db_session.commit()

    await aggregate_monthly(db_session, period)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(UsageMonthly).where(
                UsageMonthly.org_id == org.id, UsageMonthly.billing_period == period
            )
        )
    ).scalar_one()
    assert row.total_quantity == 50
    assert row.calculation_status == "calculated"
