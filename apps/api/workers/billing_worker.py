"""Billing-period close (req §16): the one workflow that ties usage
aggregation, AWS cost import, allocation, and invoice generation together
into a single, admin-triggered, idempotent-per-period action.

    1. Import AWS actual cost (no-op if disabled — see aws_cost_importer.py)
    2. Aggregate daily -> monthly usage for the period
    3. Allocate AWS cost across orgs (cost_allocation_worker.py)
    4. Compare allocated vs. actual (logged; the difference is the
       Shared/Unallocated `CostAllocation` row itself, always present)
    5. Generate one `Invoice` per org with usage
    6. Lock every `UsageMonthly` row for the period
    7. Mark invoices "final"

Refuses (raises `PeriodAlreadyLockedError`) if the period was already
locked by a previous close — closed periods are never silently reopened or
overwritten (req §16); the daily/monthly aggregators independently skip a
locked `UsageMonthly` row rather than folding a late event's numbers into
it (see workers/usage_monthly_aggregator.py::aggregate_monthly).

`create_late_event_adjustment` below is the *building block* for handling
a `UsageEvent` recorded after its period's lock — it is not yet wired into
an automatic path. Nothing in this codebase currently detects a late event
and calls it: `core/usage.py::record_usage` does not check lock status
before inserting, and no periodic sweep exists. Until that's built, a late
event is invisibly absent from that period's `Invoice`/`UsageMonthly`
totals — an operator must call this function manually (e.g. from a shell)
after noticing the discrepancy. Automating that detection needs a cost-rate
lookup to turn a raw quantity into `UsageAdjustment.amount` (a dollar
figure, unlike `UsageEvent.quantity`) — the same lookup
workers/usage_daily_aggregator.py::_unit_cost already does — and touches
the hottest function in the whole system, so it's deliberately left as a
follow-up rather than rushed into `record_usage`'s hot path here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models.invoice import Invoice
from apps.api.db.models.usage_adjustment import UsageAdjustment
from apps.api.db.models.usage_monthly import UsageMonthly
from apps.api.db.session import AsyncSessionLocal
from apps.api.workers.aws_cost_importer import import_aws_costs
from apps.api.workers.cost_allocation_worker import allocate_costs, get_org_allocated_cost
from apps.api.workers.usage_daily_aggregator import run_daily_aggregation_once
from apps.api.workers.usage_monthly_aggregator import aggregate_monthly

logger = structlog.get_logger(__name__)


class PeriodAlreadyLockedError(RuntimeError):
    """Raised by `close_billing_period` when every row for the period is
    already locked — closing is not repeatable by design (req §16)."""


async def close_billing_period(billing_period: str) -> dict[str, Any]:
    """Run the full close sequence for `billing_period` ("YYYY-MM").
    Returns a small summary dict for the admin endpoint's response."""
    async with AsyncSessionLocal() as db:
        any_unlocked = (
            await db.execute(
                select(UsageMonthly.id)
                .where(UsageMonthly.billing_period == billing_period)
                .where(UsageMonthly.calculation_status != "locked")
                .limit(1)
            )
        ).scalar_one_or_none()
        already_has_rows = (
            await db.execute(
                select(UsageMonthly.id)
                .where(UsageMonthly.billing_period == billing_period)
                .limit(1)
            )
        ).scalar_one_or_none()
        if already_has_rows is not None and any_unlocked is None:
            raise PeriodAlreadyLockedError(f"billing period {billing_period} is already locked")

    # Steps 1-2: bring usage up to date for the period being closed.
    await run_daily_aggregation_once()
    async with AsyncSessionLocal() as db:
        await aggregate_monthly(db, billing_period)
        await db.commit()

    # Step 1 (AWS): no-op unless settings.aws_cost_import_enabled.
    await import_aws_costs(billing_period)

    # Step 3: allocate AWS cost across orgs for this period.
    await allocate_costs(billing_period)

    # Steps 5-6: generate invoices, apply the platform fee, then lock.
    platform_fee = Decimal(settings.platform_fee_usd or "0")
    invoiced_orgs = 0
    async with AsyncSessionLocal() as db:
        # Third-party cost per org, summed across every service/usage_type
        # row for the period.
        org_totals = (
            await db.execute(
                select(UsageMonthly.org_id, UsageMonthly.third_party_cost).where(
                    UsageMonthly.billing_period == billing_period
                )
            )
        ).all()
        per_org: dict[UUID, Decimal] = {}
        for org_id, third_party_cost in org_totals:
            per_org[org_id] = per_org.get(org_id, Decimal("0")) + (third_party_cost or Decimal("0"))

        # Allocated AWS infrastructure cost per org — from `CostAllocation`
        # (the source of truth; NOT `UsageMonthly.allocated_aws_cost`, which
        # nothing ever populates, since allocation happens per pool
        # *category*, with no natural 1:1 mapping onto a service/usage_type
        # row — see get_org_allocated_cost's docstring). Added on top of
        # third_party_cost so an invoice actually reflects both components,
        # matching what the usage dashboards already show (routers/usage.py).
        for org_id in list(per_org):
            allocated_aws_cost = await get_org_allocated_cost(db, org_id, billing_period)
            per_org[org_id] += allocated_aws_cost

        for org_id, usage_charge in per_org.items():
            total_amount = usage_charge + platform_fee
            existing_invoice = (
                await db.execute(
                    select(Invoice).where(
                        Invoice.org_id == org_id, Invoice.billing_period == billing_period
                    )
                )
            ).scalar_one_or_none()
            if existing_invoice is not None and existing_invoice.status == "final":
                continue
            if existing_invoice is None:
                existing_invoice = Invoice(org_id=org_id, billing_period=billing_period)
                db.add(existing_invoice)
            existing_invoice.usage_charge = usage_charge
            existing_invoice.platform_fee = platform_fee
            existing_invoice.total_amount = total_amount
            existing_invoice.status = "final"
            existing_invoice.locked_at = datetime.now(UTC)
            invoiced_orgs += 1

        await db.execute(
            update(UsageMonthly)
            .where(UsageMonthly.billing_period == billing_period)
            .values(calculation_status="locked")
        )
        await db.commit()

    logger.info("billing_period_closed", period=billing_period, invoiced_orgs=invoiced_orgs)
    return {"billing_period": billing_period, "invoiced_orgs": invoiced_orgs}


async def create_late_event_adjustment(
    db: AsyncSession,
    *,
    org_id: UUID,
    billing_period: str,
    event_id: str,
    amount: Decimal,
    notes: str | None = None,
) -> UsageAdjustment:
    """Record a `UsageAdjustment` for a `UsageEvent` that arrived after
    `billing_period` was already locked, instead of ever mutating the
    locked `UsageMonthly`/`Invoice` rows (req §16)."""
    adjustment = UsageAdjustment(
        org_id=org_id,
        billing_period=billing_period,
        reason="late_event",
        amount=amount,
        related_event_id=event_id,
        notes=notes,
    )
    db.add(adjustment)
    await db.flush()
    return adjustment
