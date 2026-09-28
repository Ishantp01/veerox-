"""Covers workers/cost_allocation_worker.py: the req §10 formula
(AllocatedCost = PoolTotalCost * OrgMetric / TotalMetric), the reserved
Shared/Unallocated remainder row, and allocation_version bumping instead of
overwriting a prior run."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.db.models import Org
from apps.api.db.models.aws_cost_pool import AwsCostPool
from apps.api.db.models.cost_allocation import CostAllocation
from apps.api.db.models.usage_monthly import UsageMonthly

BILLING_PERIOD = "2026-09"


@pytest.fixture(autouse=True)
def _redirect_worker_sessions(test_engine, monkeypatch: pytest.MonkeyPatch):
    from apps.api.workers import cost_allocation_worker

    test_session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(cost_allocation_worker, "AsyncSessionLocal", test_session_factory)


async def test_allocation_splits_pool_proportionally_and_leaves_remainder(
    db_session: AsyncSession,
) -> None:
    from apps.api.workers.cost_allocation_worker import allocate_costs

    org_a = Org(name="Heavy Co")
    org_b = Org(name="Light Co")
    db_session.add_all([org_a, org_b])
    await db_session.flush()

    db_session.add(
        AwsCostPool(
            billing_period=BILLING_PERIOD,
            aws_account_id="123456789012",
            aws_service="Amazon Elastic Compute Cloud - Compute",
            pool_category="compute",
            total_cost=Decimal("100.00"),
            currency="USD",
            source="manual",
        )
    )
    # org_a used 3x org_b's compute metric -> should get 3x the allocation.
    db_session.add_all(
        [
            UsageMonthly(
                org_id=org_a.id,
                billing_period=BILLING_PERIOD,
                service="compute",
                usage_type="worker_job_seconds",
                total_quantity=Decimal("300"),
                calculation_status="calculated",
            ),
            UsageMonthly(
                org_id=org_b.id,
                billing_period=BILLING_PERIOD,
                service="compute",
                usage_type="worker_job_seconds",
                total_quantity=Decimal("100"),
                calculation_status="calculated",
            ),
        ]
    )
    await db_session.commit()

    written = await allocate_costs(BILLING_PERIOD)
    assert written == 3  # org_a + org_b + the shared/unallocated remainder row

    rows = (
        await db_session.execute(
            select(CostAllocation).where(CostAllocation.billing_period == BILLING_PERIOD)
        )
    ).scalars().all()
    by_org = {r.org_id: r.allocated_cost for r in rows}

    assert by_org[org_a.id] == Decimal("75.0000")
    assert by_org[org_b.id] == Decimal("25.0000")
    # The reserved Shared/Unallocated row is always present, even when
    # (as here) every dollar was attributed to a real org.
    assert by_org[None] == Decimal("0.0000")
    # sum(allocated_cost) across the group reconciles exactly to pool total.
    assert sum(by_org.values()) == Decimal("100.0000")


async def test_zero_metric_pool_goes_entirely_to_shared_unallocated(
    db_session: AsyncSession,
) -> None:
    from apps.api.workers.cost_allocation_worker import allocate_costs

    db_session.add(
        AwsCostPool(
            billing_period=BILLING_PERIOD,
            aws_account_id="123456789012",
            aws_service="Amazon Relational Database Service",
            pool_category="database",
            total_cost=Decimal("50.00"),
            currency="USD",
            source="manual",
        )
    )
    await db_session.commit()

    await allocate_costs(BILLING_PERIOD)

    row = (
        await db_session.execute(
            select(CostAllocation).where(
                CostAllocation.billing_period == BILLING_PERIOD,
                CostAllocation.pool_category == "database",
            )
        )
    ).scalar_one()
    assert row.org_id is None
    assert row.allocated_cost == Decimal("50.0000")


async def test_rerunning_allocation_bumps_version_instead_of_overwriting(
    db_session: AsyncSession,
) -> None:
    from apps.api.workers.cost_allocation_worker import allocate_costs

    org = Org(name="Versioned Co")
    db_session.add(org)
    await db_session.flush()
    db_session.add(
        AwsCostPool(
            billing_period=BILLING_PERIOD,
            aws_account_id="123456789012",
            aws_service="AWS Lambda",
            pool_category="serverless",
            total_cost=Decimal("10.00"),
            currency="USD",
            source="manual",
        )
    )
    db_session.add(
        UsageMonthly(
            org_id=org.id,
            billing_period=BILLING_PERIOD,
            service="compute",
            usage_type="invocation_count",
            total_quantity=Decimal("1"),
            calculation_status="calculated",
        )
    )
    await db_session.commit()

    await allocate_costs(BILLING_PERIOD)
    await allocate_costs(BILLING_PERIOD)  # a second run for the same period

    versions = (
        await db_session.execute(
            select(CostAllocation.allocation_version)
            .where(CostAllocation.billing_period == BILLING_PERIOD)
            .distinct()
        )
    ).scalars().all()
    assert sorted(versions) == [1, 2]


async def test_get_org_allocated_cost_uses_latest_version_only(db_session: AsyncSession) -> None:
    """Regression test: summing an org's allocated cost across every
    `allocation_version` (instead of just the latest) would double-count
    it once allocation has ever re-run for the same period."""
    from apps.api.workers.cost_allocation_worker import get_org_allocated_cost

    org = Org(name="Reallocated Co")
    db_session.add(org)
    await db_session.flush()
    db_session.add_all(
        [
            CostAllocation(
                billing_period=BILLING_PERIOD,
                org_id=org.id,
                pool_category="compute",
                allocated_cost=Decimal("10.00"),
                metric_used="compute.worker_job_seconds",
                metric_value=Decimal("1"),
                total_metric_value=Decimal("1"),
                allocation_version=1,
            ),
            CostAllocation(
                billing_period=BILLING_PERIOD,
                org_id=org.id,
                pool_category="compute",
                allocated_cost=Decimal("15.00"),
                metric_used="compute.worker_job_seconds",
                metric_value=Decimal("1"),
                total_metric_value=Decimal("1"),
                allocation_version=2,
            ),
        ]
    )
    await db_session.commit()

    total = await get_org_allocated_cost(db_session, org.id, BILLING_PERIOD)
    assert total == Decimal("15.00")  # only version 2, not 10 + 15
