"""Covers workers/billing_worker.py: the period-close sequence, refusing to
re-close an already-locked period, a late event after lock producing a
UsageAdjustment instead of mutating the locked UsageMonthly/Invoice rows
(req §16), and the automatic late-event sweep that finds such events and
adjusts them without an operator having to call create_late_event_adjustment
by hand."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.core.usage import deterministic_event_id, record_usage
from apps.api.db.models import Org
from apps.api.db.models.cost_allocation import CostAllocation
from apps.api.db.models.cost_rate import CostRate
from apps.api.db.models.invoice import Invoice
from apps.api.db.models.usage_adjustment import UsageAdjustment
from apps.api.db.models.usage_event import UsageEvent
from apps.api.db.models.usage_monthly import UsageMonthly

BILLING_PERIOD = "2026-08"


@pytest.fixture(autouse=True)
def _redirect_worker_sessions(test_engine, monkeypatch: pytest.MonkeyPatch):
    from apps.api.workers import (
        aws_cost_importer,
        billing_worker,
        cost_allocation_worker,
        usage_daily_aggregator,
        usage_monthly_aggregator,
    )

    test_session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    for module in (
        aws_cost_importer,
        billing_worker,
        cost_allocation_worker,
        usage_daily_aggregator,
        usage_monthly_aggregator,
    ):
        monkeypatch.setattr(module, "AsyncSessionLocal", test_session_factory)


async def test_close_billing_period_locks_and_invoices(db_session: AsyncSession) -> None:
    from apps.api.workers.billing_worker import close_billing_period

    org = Org(name="Closing Co")
    db_session.add(org)
    await db_session.flush()
    db_session.add(
        UsageMonthly(
            org_id=org.id,
            billing_period=BILLING_PERIOD,
            service="ai",
            usage_type="ai_input_tokens",
            total_quantity=Decimal("1000"),
            third_party_cost=Decimal("2.50"),
            calculation_status="calculated",
        )
    )
    await db_session.commit()

    result = await close_billing_period(BILLING_PERIOD)
    assert result["billing_period"] == BILLING_PERIOD
    assert result["invoiced_orgs"] == 1

    row = (
        await db_session.execute(
            select(UsageMonthly).where(
                UsageMonthly.org_id == org.id, UsageMonthly.billing_period == BILLING_PERIOD
            )
        )
    ).scalar_one()
    assert row.calculation_status == "locked"

    invoice = (
        await db_session.execute(
            select(Invoice).where(
                Invoice.org_id == org.id, Invoice.billing_period == BILLING_PERIOD
            )
        )
    ).scalar_one()
    assert invoice.status == "final"
    assert invoice.usage_charge == Decimal("2.50")


async def test_close_billing_period_invoice_includes_allocated_aws_cost(
    db_session: AsyncSession,
) -> None:
    """Regression test: the invoice's usage_charge must include the org's
    allocated AWS cost (from `CostAllocation`), not just third_party_cost —
    `UsageMonthly.allocated_aws_cost` is never populated by anything (see
    db/models/usage_monthly.py), so billing_worker.py must read allocated
    cost via get_org_allocated_cost like the dashboards do, not that
    always-NULL column."""
    from apps.api.workers.billing_worker import close_billing_period

    org = Org(name="Allocated Co")
    db_session.add(org)
    await db_session.flush()
    db_session.add(
        UsageMonthly(
            org_id=org.id,
            billing_period=BILLING_PERIOD,
            service="ai",
            usage_type="ai_input_tokens",
            total_quantity=Decimal("1000"),
            third_party_cost=Decimal("2.50"),
            calculation_status="calculated",
        )
    )
    db_session.add(
        CostAllocation(
            billing_period=BILLING_PERIOD,
            org_id=org.id,
            pool_category="compute",
            allocated_cost=Decimal("7.75"),
            metric_used="compute.worker_job_seconds",
            metric_value=Decimal("100"),
            total_metric_value=Decimal("100"),
            allocation_version=1,
        )
    )
    await db_session.commit()

    await close_billing_period(BILLING_PERIOD)

    invoice = (
        await db_session.execute(
            select(Invoice).where(
                Invoice.org_id == org.id, Invoice.billing_period == BILLING_PERIOD
            )
        )
    ).scalar_one()
    assert invoice.usage_charge == Decimal("10.25")  # 2.50 third-party + 7.75 allocated AWS


async def test_closing_an_already_locked_period_raises(db_session: AsyncSession) -> None:
    from apps.api.workers.billing_worker import PeriodAlreadyLockedError, close_billing_period

    org = Org(name="Already Closed Co")
    db_session.add(org)
    await db_session.flush()
    db_session.add(
        UsageMonthly(
            org_id=org.id,
            billing_period=BILLING_PERIOD,
            service="ai",
            usage_type="ai_input_tokens",
            total_quantity=Decimal("10"),
            calculation_status="locked",
        )
    )
    await db_session.commit()

    with pytest.raises(PeriodAlreadyLockedError):
        await close_billing_period(BILLING_PERIOD)


async def test_late_event_after_lock_creates_adjustment_not_a_mutation(
    db_session: AsyncSession,
) -> None:
    """A UsageEvent that lands after the period is locked must never change
    the locked UsageMonthly row — it becomes a UsageAdjustment instead."""
    from apps.api.workers.billing_worker import create_late_event_adjustment

    org = Org(name="Late Event Co")
    db_session.add(org)
    await db_session.flush()
    locked_row = UsageMonthly(
        org_id=org.id,
        billing_period=BILLING_PERIOD,
        service="voice",
        usage_type="voice_seconds",
        total_quantity=Decimal("500"),
        calculation_status="locked",
    )
    db_session.add(locked_row)
    await db_session.commit()

    late_event_id = deterministic_event_id("voice_call_ended", "late-call-999")
    await record_usage(
        db_session,
        organization_id=org.id,
        event_id=late_event_id,
        service="voice",
        usage_type="voice_seconds",
        quantity=30,
        unit="seconds",
        source="test",
    )
    await create_late_event_adjustment(
        db_session,
        org_id=org.id,
        billing_period=BILLING_PERIOD,
        event_id=late_event_id,
        amount=Decimal("30"),
        notes="late voice_seconds event after period lock",
    )
    await db_session.commit()

    await db_session.refresh(locked_row)
    assert locked_row.total_quantity == Decimal("500")  # untouched
    assert locked_row.calculation_status == "locked"

    adjustment = (
        await db_session.execute(
            select(UsageAdjustment).where(UsageAdjustment.org_id == org.id)
        )
    ).scalar_one()
    assert adjustment.reason == "late_event"
    assert adjustment.amount == Decimal("30")
    assert adjustment.related_event_id == late_event_id


async def _make_locked_invoice(
    db_session: AsyncSession, *, org: Org, billing_period: str, locked_at: datetime
) -> Invoice:
    invoice = Invoice(
        org_id=org.id,
        billing_period=billing_period,
        usage_charge=Decimal("0"),
        platform_fee=Decimal("0"),
        total_amount=Decimal("0"),
        status="final",
        locked_at=locked_at,
    )
    db_session.add(invoice)
    await db_session.flush()
    return invoice


async def test_sweep_adjusts_event_that_landed_after_an_early_period_close(
    db_session: AsyncSession,
) -> None:
    """The sweep's real, honest scope (see billing_worker.py's module
    docstring): a period closed *before* its calendar month ended (closing
    is admin-triggered, not restricted to month-end), followed by a
    legitimately-dated event for the same month arriving afterward."""
    from apps.api.workers.billing_worker import run_late_event_sweep_once

    org = Org(name="Early Close Co")
    db_session.add(org)
    await db_session.flush()
    await _make_locked_invoice(
        db_session,
        org=org,
        billing_period="2026-08",
        locked_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    db_session.add(
        CostRate(
            service="voice",
            usage_type="voice_seconds",
            unit_cost=Decimal("0.01"),
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    late_event_id = deterministic_event_id("voice_call_ended", "straggler-1")
    db_session.add(
        UsageEvent(
            event_id=late_event_id,
            org_id=org.id,
            service="voice",
            usage_type="voice_seconds",
            quantity=Decimal("200"),
            unit="seconds",
            source="test",
            created_at=datetime(2026, 8, 25, tzinfo=UTC),  # after locked_at, still in August
        )
    )
    await db_session.commit()

    created = await run_late_event_sweep_once()
    assert created == 1

    adjustment = (
        await db_session.execute(
            select(UsageAdjustment).where(UsageAdjustment.org_id == org.id)
        )
    ).scalar_one()
    assert adjustment.reason == "late_event"
    assert adjustment.related_event_id == late_event_id
    assert adjustment.amount == Decimal("2.00")  # 200 seconds * 0.01


async def test_sweep_is_idempotent_across_repeated_runs(db_session: AsyncSession) -> None:
    from apps.api.workers.billing_worker import run_late_event_sweep_once

    org = Org(name="Idempotent Sweep Co")
    db_session.add(org)
    await db_session.flush()
    await _make_locked_invoice(
        db_session,
        org=org,
        billing_period="2026-08",
        locked_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    db_session.add(
        UsageEvent(
            event_id=deterministic_event_id("whatsapp_inbound", "straggler-2"),
            org_id=org.id,
            service="whatsapp",
            usage_type="whatsapp_messages",
            quantity=Decimal("1"),
            unit="messages",
            source="test",
            created_at=datetime(2026, 8, 15, tzinfo=UTC),
        )
    )
    await db_session.commit()

    first_run = await run_late_event_sweep_once()
    second_run = await run_late_event_sweep_once()
    assert first_run == 1
    assert second_run == 0  # already adjusted — not double-counted

    count = (
        await db_session.execute(select(UsageAdjustment).where(UsageAdjustment.org_id == org.id))
    ).scalars().all()
    assert len(count) == 1


async def test_sweep_ignores_events_from_before_the_lock(db_session: AsyncSession) -> None:
    """An event that was already counted (created_at before locked_at) is
    not "late" — it must not get a spurious duplicate adjustment."""
    from apps.api.workers.billing_worker import run_late_event_sweep_once

    org = Org(name="Not Late Co")
    db_session.add(org)
    await db_session.flush()
    await _make_locked_invoice(
        db_session,
        org=org,
        billing_period="2026-08",
        locked_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    db_session.add(
        UsageEvent(
            event_id=deterministic_event_id("whatsapp_inbound", "already-counted"),
            org_id=org.id,
            service="whatsapp",
            usage_type="whatsapp_messages",
            quantity=Decimal("1"),
            unit="messages",
            source="test",
            created_at=datetime(2026, 8, 5, tzinfo=UTC),  # before locked_at
        )
    )
    await db_session.commit()

    created = await run_late_event_sweep_once()
    assert created == 0


async def test_sweep_records_unpriced_late_event_as_zero_with_a_note(
    db_session: AsyncSession,
) -> None:
    """No CostRate configured yet must not silently drop the event — it's
    still recorded (amount 0, noted as unpriced) so an operator can see it
    happened and backfill a rate later."""
    from apps.api.workers.billing_worker import run_late_event_sweep_once

    org = Org(name="Unpriced Co")
    db_session.add(org)
    await db_session.flush()
    await _make_locked_invoice(
        db_session,
        org=org,
        billing_period="2026-08",
        locked_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    late_event_id = deterministic_event_id("db_op", "unpriced-1")
    db_session.add(
        UsageEvent(
            event_id=late_event_id,
            org_id=org.id,
            service="database",
            usage_type="database_operations",
            quantity=Decimal("500"),
            unit="ops",
            source="test",
            created_at=datetime(2026, 8, 15, tzinfo=UTC),
        )
    )
    await db_session.commit()

    created = await run_late_event_sweep_once()
    assert created == 1

    adjustment = (
        await db_session.execute(
            select(UsageAdjustment).where(UsageAdjustment.related_event_id == late_event_id)
        )
    ).scalar_one()
    assert adjustment.amount == Decimal("0")
    assert adjustment.notes is not None
    assert "no CostRate" in adjustment.notes
