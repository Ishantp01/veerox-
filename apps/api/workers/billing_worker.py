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
a `UsageEvent` recorded after its period's lock. It is wired into an
automatic path: `run_late_event_adjustment_sweep`, a poll loop (same
asyncio-in-lifespan pattern as every other worker here, registered in
main.py) that periodically scans every `Invoice` locked within the last
`_SWEEP_LOOKBACK_DAYS` days for `UsageEvent` rows whose `created_at` falls
inside that invoice's `billing_period` calendar month but *after* the
invoice's own `locked_at`, and creates one `UsageAdjustment` per such event
it hasn't already adjusted (dedup via
`(org_id, billing_period, reason="late_event", related_event_id)` — an
event already adjusted in a prior sweep is skipped, making repeated sweeps
idempotent).

Scope, stated precisely because it's easy to overclaim here: `UsageEvent.
created_at` is a server-assigned insert timestamp (`func.now()`), never
caller-supplied or backdated (the ledger's immutability guarantee), and
both aggregators already bucket purely by `created_at`
(usage_daily_aggregator.py's `_aggregate_day`). That means this sweep only
ever finds something when a period was closed *before* its calendar month
actually ended — e.g. an admin closes "2026-08" on August 20th (closing is
admin-triggered per req §16, not restricted to month-end) and further
August-dated events keep landing afterward. It does **not** catch a
delayed event whose processing lag pushes its `created_at` past the
following month's start (e.g. work done Aug 31 but only recorded Sep 2,
after a normal end-of-month close already ran on Sep 1) — that event's
`created_at` is in September, so it's simply, correctly counted as
September usage by the next monthly aggregation; there is no gap to sweep,
only a (documented, pre-existing) attribution choice inherent in using
`created_at` as the aggregation key everywhere in this system, not
something this sweep can or should second-guess by inventing a business
timestamp none of the call sites currently supply.

`core/usage.py::record_usage` itself still does not check lock status
before inserting — deliberately: rejecting a late insert there would lose
the event outright, whereas this sweep preserves it as a dollar-valued
adjustment on the next available invoice, without ever touching the locked
row. An event with no matching `CostRate` at sweep time is still recorded
(amount `0`, noted as unpriced) rather than silently dropped, so an
operator can see it happened and backfill a rate later; the row is *not*
auto-repriced once a rate does appear (repricing an already-created
adjustment would reopen the same "silently changes a locked figure"
problem this whole mechanism exists to avoid) — a manual `reason="manual"`
adjustment covers that case if it's ever needed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models.invoice import Invoice
from apps.api.db.models.usage_adjustment import UsageAdjustment
from apps.api.db.models.usage_event import UsageEvent
from apps.api.db.models.usage_monthly import UsageMonthly
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import record_error
from apps.api.workers.aws_cost_importer import import_aws_costs
from apps.api.workers.cost_allocation_worker import allocate_costs, get_org_allocated_cost
from apps.api.workers.usage_daily_aggregator import _unit_cost, run_daily_aggregation_once
from apps.api.workers.usage_monthly_aggregator import aggregate_monthly

logger = structlog.get_logger(__name__)

# How often the sweep runs, and how far back it looks for locked invoices to
# re-check. 6 hours is frequent enough that a late event shows up on an
# org's next statement within the same business day without being a hot
# poll loop; 90 days bounds the query to recently-closed periods rather than
# rescanning the platform's entire invoice history every tick (an event
# arriving months after its period locked is vanishingly unlikely for the
# "webhook/worker retry landed a bit late" case this exists for — an
# operator handles that rarer case with a manual `reason="manual"`
# adjustment instead). Both are plain module constants, matching every
# other worker's `_POLL_INTERVAL_SECS`/`_LOOKBACK_DAYS` convention in this
# package — change here if the policy needs tuning.
_SWEEP_INTERVAL_SECS = 6 * 3600
_SWEEP_LOOKBACK_DAYS = 90


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


async def _sweep_late_events_for_invoice(db: AsyncSession, invoice: Invoice) -> int:
    """Find `UsageEvent` rows for `invoice`'s org that landed inside its
    billing period's calendar month but after the invoice was locked, and
    create a `UsageAdjustment` for each one not already adjusted. Returns
    the number of adjustments created."""
    if invoice.locked_at is None:
        return 0
    year, month = (int(part) for part in invoice.billing_period.split("-"))
    period_start = datetime(year, month, 1, tzinfo=UTC)
    period_end = (period_start + timedelta(days=32)).replace(day=1)

    already_adjusted = set(
        (
            await db.execute(
                select(UsageAdjustment.related_event_id).where(
                    UsageAdjustment.org_id == invoice.org_id,
                    UsageAdjustment.billing_period == invoice.billing_period,
                    UsageAdjustment.reason == "late_event",
                )
            )
        )
        .scalars()
        .all()
    )

    events = (
        (
            await db.execute(
                select(UsageEvent).where(
                    UsageEvent.org_id == invoice.org_id,
                    UsageEvent.created_at >= period_start,
                    UsageEvent.created_at < period_end,
                    UsageEvent.created_at > invoice.locked_at,
                )
            )
        )
        .scalars()
        .all()
    )

    created = 0
    for event in events:
        if event.event_id in already_adjusted:
            continue
        cost = await _unit_cost(
            db, event.service, event.usage_type, event.provider, event.created_at
        )
        if cost is not None:
            amount = cost * event.quantity
            notes = "auto-created by late_event_adjustment_sweep"
        else:
            amount = Decimal("0")
            notes = (
                "auto-created by late_event_adjustment_sweep; no CostRate was "
                "configured at sweep time, amount recorded as 0"
            )
        await create_late_event_adjustment(
            db,
            org_id=invoice.org_id,
            billing_period=invoice.billing_period,
            event_id=event.event_id,
            amount=amount,
            notes=notes,
        )
        created += 1
    return created


async def run_late_event_sweep_once() -> int:
    """Scan every `Invoice` locked within `_SWEEP_LOOKBACK_DAYS` for late
    `UsageEvent`s and adjust them. Exposed separately from the poll loop so
    tests and an admin trigger can run it synchronously. Returns the total
    number of `UsageAdjustment` rows created."""
    cutoff = datetime.now(UTC) - timedelta(days=_SWEEP_LOOKBACK_DAYS)
    total = 0
    async with AsyncSessionLocal() as db:
        invoices = (
            (
                await db.execute(
                    select(Invoice).where(
                        Invoice.status == "final",
                        Invoice.locked_at.is_not(None),
                        Invoice.locked_at >= cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )
        for invoice in invoices:
            total += await _sweep_late_events_for_invoice(db, invoice)
        await db.commit()
    return total


async def run_late_event_adjustment_sweep() -> None:
    """The worker's main loop — runs for the lifetime of the app process,
    mirroring workers/usage_daily_aggregator.py's poll-loop structure."""
    while True:
        try:
            count = await run_late_event_sweep_once()
            logger.info("late_event_adjustment_sweep_tick", adjustments_created=count)
        except Exception:  # noqa: BLE001
            logger.exception("late_event_adjustment_sweep_tick_failed")
            await record_error()
        await asyncio.sleep(_SWEEP_INTERVAL_SECS)
