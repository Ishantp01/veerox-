"""Splits each `AwsCostPool` category's actual AWS spend across organizations
for a billing period, per req §10's documented formula:

    AllocatedCost = PoolTotalCost * OrgMetric / TotalMetricForAllOrgs

Metrics are pulled from that period's `UsageMonthly` rows (already computed
by workers/usage_monthly_aggregator.py) using one documented proxy per
category — see `_CATEGORY_METRIC`. An org with zero measured metric for a
category gets zero allocation from it; any remainder (rounding, a pool with
no organizations reporting a metric at all) is written to the reserved
`org_id IS NULL` "Shared/Unallocated" row so `sum(allocated_cost)` for a
(period, category, version) always reconciles to the pool's `total_cost`.

Never mutates a previous run's rows: every call bumps `allocation_version`
(one higher than the max seen for that period) and inserts a fresh set,
preserving req §10's "do not silently change allocation rules between
billing periods".
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.aws_cost_pool import AwsCostPool
from apps.api.db.models.cost_allocation import CostAllocation
from apps.api.db.models.usage_monthly import UsageMonthly
from apps.api.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)

# pool_category -> the UsageMonthly (service, usage_type) whose
# total_quantity is used as this category's proxy metric. Documented here
# (req §10 requires the metric be explicit, not implicit) rather than
# computed ad hoc.
_CATEGORY_METRIC: dict[str, tuple[str, str]] = {
    "compute": ("compute", "worker_job_seconds"),
    "database": ("database", "database_operations"),
    "storage": ("storage", "storage_gb_month"),
    "network": ("network", "bandwidth_gb"),
    "serverless": ("compute", "invocation_count"),
    "queue": ("queue", "invocation_count"),
    "logging": ("compute", "worker_job_seconds"),
    "misc": ("compute", "worker_job_seconds"),
}


async def _org_metric_values(
    db: AsyncSession, billing_period: str, service: str, usage_type: str
) -> dict[UUID, Decimal]:
    stmt = (
        select(UsageMonthly.org_id, func.sum(UsageMonthly.total_quantity))
        .where(
            UsageMonthly.billing_period == billing_period,
            UsageMonthly.service == service,
            UsageMonthly.usage_type == usage_type,
        )
        .group_by(UsageMonthly.org_id)
    )
    return {row[0]: row[1] for row in (await db.execute(stmt)).all()}


async def allocate_costs(billing_period: str) -> int:
    """Compute and persist `CostAllocation` rows for every `AwsCostPool`
    category present in `billing_period`. Returns the number of rows
    written (org rows + one shared/unallocated row per category)."""
    async with AsyncSessionLocal() as db:
        pools = (
            await db.execute(
                select(AwsCostPool.pool_category, func.sum(AwsCostPool.total_cost))
                .where(AwsCostPool.billing_period == billing_period)
                .group_by(AwsCostPool.pool_category)
            )
        ).all()
        if not pools:
            logger.info("cost_allocation_worker_no_pools", period=billing_period)
            return 0

        prior_version = (
            await db.execute(
                select(func.max(CostAllocation.allocation_version)).where(
                    CostAllocation.billing_period == billing_period
                )
            )
        ).scalar_one_or_none()
        version = (prior_version or 0) + 1

        written = 0
        for pool_category, pool_total_cost in pools:
            service, usage_type = _CATEGORY_METRIC[pool_category]
            org_metrics = await _org_metric_values(db, billing_period, service, usage_type)
            total_metric = sum(org_metrics.values(), Decimal("0"))

            allocated_sum = Decimal("0")
            if total_metric > 0:
                for org_id, metric_value in org_metrics.items():
                    share = (pool_total_cost * metric_value / total_metric).quantize(
                        Decimal("0.0001"), rounding=ROUND_HALF_UP
                    )
                    allocated_sum += share
                    db.add(
                        CostAllocation(
                            billing_period=billing_period,
                            org_id=org_id,
                            pool_category=pool_category,
                            allocated_cost=share,
                            metric_used=f"{service}.{usage_type}",
                            metric_value=metric_value,
                            total_metric_value=total_metric,
                            is_estimated=True,
                            allocation_version=version,
                        )
                    )
                    written += 1

            # The reserved Shared/Unallocated row: whatever's left after
            # every org's share (zero orgs measured, or a rounding
            # remainder) — never omitted, so the category's allocations
            # always reconcile to pool_total_cost exactly.
            remainder = pool_total_cost - allocated_sum
            db.add(
                CostAllocation(
                    billing_period=billing_period,
                    org_id=None,
                    pool_category=pool_category,
                    allocated_cost=remainder,
                    metric_used=f"{service}.{usage_type}",
                    metric_value=Decimal("0"),
                    total_metric_value=total_metric,
                    is_estimated=True,
                    allocation_version=version,
                )
            )
            written += 1

        await db.commit()
        logger.info(
            "cost_allocation_worker_allocated",
            period=billing_period,
            version=version,
            rows=written,
        )
        return written


async def get_org_allocated_cost(db: AsyncSession, org_id: UUID, billing_period: str) -> Decimal:
    """This org's total estimated share of shared AWS cost for the period,
    summed across every `CostAllocation` category row **for the latest
    `allocation_version` only**.

    `CostAllocation` (not `UsageMonthly.allocated_aws_cost`) is the single
    source of truth for allocated cost — allocation happens per pool
    *category*, which has no natural 1:1 mapping onto a `UsageMonthly` row
    (keyed by *service/usage_type*), so nothing ever populates that column.
    Every reader of an org's allocated cost (the usage dashboards in
    routers/usage.py, and the invoice totals in workers/billing_worker.py)
    must go through this one function so they can never drift apart.

    Filtering to the max version matters: `allocate_costs` never overwrites
    a prior run (req §10) — it inserts a fresh set of rows under a bumped
    `allocation_version` — so summing across every version present would
    double/triple-count an org's cost if allocation ever ran more than
    once for the same period (e.g. a retried period close).
    """
    latest_version = (
        await db.execute(
            select(func.max(CostAllocation.allocation_version)).where(
                CostAllocation.billing_period == billing_period
            )
        )
    ).scalar_one_or_none()
    if latest_version is None:
        return Decimal("0")
    rows = (
        await db.execute(
            select(CostAllocation.allocated_cost).where(
                CostAllocation.org_id == org_id,
                CostAllocation.billing_period == billing_period,
                CostAllocation.allocation_version == latest_version,
            )
        )
    ).scalars().all()
    return sum(rows, Decimal("0"))
