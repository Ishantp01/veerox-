"""Usage/billing read endpoints (req §14) — org self-service usage +
estimate, platform-admin cross-org usage/AWS-cost views, internal usage-
event ingestion, and billing-period close.

Org id is always server-resolved (`RequestOrgDep`/`verify_admin_or_session`,
same as every other router — see deps.py) — nothing here ever trusts an
`org_id` a client supplies in the path or body for its *own* data. The one
path parameter that names an org (`/admin/organizations/{org_id}/usage`) is
gated by `verify_platform_admin`, which only a platform superuser or the
shared `X-Admin-Token` satisfies.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select

from apps.api.config import settings
from apps.api.db.models.aws_cost_pool import AwsCostPool
from apps.api.db.models.cost_allocation import CostAllocation
from apps.api.db.models.cost_rate import CostRate
from apps.api.db.models.invoice import Invoice
from apps.api.db.models.org import Org
from apps.api.db.models.usage_monthly import UsageMonthly
from apps.api.deps import DbDep, RequestOrgDep, verify_admin_or_session, verify_platform_admin
from apps.api.schemas.usage import (
    AdminAwsCostsOut,
    AdminOrgUsageOut,
    AwsCostPoolOut,
    BillingHistoryRow,
    BillingPeriodCloseOut,
    CostRateIn,
    CostRateOut,
    OrgBillingEstimateOut,
    OrgUsageOut,
    UsageBreakdownRow,
    UsageEventIn,
)
from apps.api.workers.billing_worker import PeriodAlreadyLockedError, close_billing_period
from apps.api.workers.cost_allocation_worker import get_org_allocated_cost
from apps.api.workers.usage_monthly_aggregator import current_billing_period

router = APIRouter(
    prefix="/org", tags=["usage"], dependencies=[Depends(verify_admin_or_session)]
)
admin_router = APIRouter(
    prefix="/admin", tags=["admin-usage"], dependencies=[Depends(verify_platform_admin)]
)
internal_router = APIRouter(prefix="/internal", tags=["internal"])


def _resolve_period(period: str) -> str:
    return current_billing_period() if period == "current" else period


async def _org_breakdown_rows(
    db: DbDep, org_id: UUID, billing_period: str
) -> Sequence[UsageMonthly]:
    return (
        await db.execute(
            select(UsageMonthly).where(
                UsageMonthly.org_id == org_id, UsageMonthly.billing_period == billing_period
            )
        )
    ).scalars().all()


def _breakdown_rows_out(rows: Sequence[UsageMonthly]) -> list[UsageBreakdownRow]:
    return [
        UsageBreakdownRow(
            service=r.service,
            usage_type=r.usage_type,
            total_quantity=float(r.total_quantity),
            estimated_cost=float(r.third_party_cost) if r.third_party_cost is not None else None,
        )
        for r in rows
    ]


def _third_party_total(rows: Sequence[UsageMonthly]) -> Decimal:
    return sum((r.third_party_cost or Decimal("0") for r in rows), Decimal("0"))


@router.get("/usage", response_model=OrgUsageOut)
async def get_org_usage(
    db: DbDep, org_id: RequestOrgDep, period: str = Query("current")
) -> OrgUsageOut:
    billing_period = _resolve_period(period)
    rows = await _org_breakdown_rows(db, org_id, billing_period)
    breakdown = _breakdown_rows_out(rows)
    third_party_total = _third_party_total(rows)
    allocated_aws = await get_org_allocated_cost(db, org_id, billing_period)
    platform_fee = Decimal(settings.platform_fee_usd or "0")

    status_rows = (
        await db.execute(
            select(UsageMonthly.calculation_status)
            .where(UsageMonthly.org_id == org_id, UsageMonthly.billing_period == billing_period)
            .limit(1)
        )
    ).scalar_one_or_none()

    return OrgUsageOut(
        org_id=str(org_id),
        billing_period=billing_period,
        breakdown=breakdown,
        third_party_cost_total=float(third_party_total),
        estimated_infrastructure_usage=float(allocated_aws),
        platform_fee=float(platform_fee),
        estimated_total_charge=float(third_party_total + allocated_aws + platform_fee),
        calculation_status=status_rows or "pending",
    )


@router.get("/usage/breakdown", response_model=list[UsageBreakdownRow])
async def get_org_usage_breakdown(
    db: DbDep, org_id: RequestOrgDep, period: str = Query("current")
) -> list[UsageBreakdownRow]:
    rows = await _org_breakdown_rows(db, org_id, _resolve_period(period))
    return _breakdown_rows_out(rows)


@router.get("/billing/estimate", response_model=OrgBillingEstimateOut)
async def get_org_billing_estimate(
    db: DbDep, org_id: RequestOrgDep, period: str = Query("current")
) -> OrgBillingEstimateOut:
    billing_period = _resolve_period(period)
    rows = await _org_breakdown_rows(db, org_id, billing_period)
    third_party_total = _third_party_total(rows)
    allocated_aws = await get_org_allocated_cost(db, org_id, billing_period)
    platform_fee = Decimal(settings.platform_fee_usd or "0")

    invoices = (
        await db.execute(
            select(Invoice)
            .where(Invoice.org_id == org_id)
            .order_by(Invoice.billing_period.desc())
            .limit(12)
        )
    ).scalars().all()

    return OrgBillingEstimateOut(
        org_id=str(org_id),
        billing_period=billing_period,
        usage_charge_estimate=float(third_party_total),
        estimated_infrastructure_usage=float(allocated_aws),
        platform_fee=float(platform_fee),
        estimated_total_charge=float(third_party_total + allocated_aws + platform_fee),
        history=[
            BillingHistoryRow(
                billing_period=inv.billing_period,
                usage_charge=float(inv.usage_charge),
                platform_fee=float(inv.platform_fee),
                total_amount=float(inv.total_amount),
                status=inv.status,
            )
            for inv in invoices
        ],
    )


@admin_router.get("/organizations/{org_id}/usage", response_model=AdminOrgUsageOut)
async def admin_get_org_usage(
    db: DbDep, org_id: UUID, period: str = Query("current")
) -> AdminOrgUsageOut:
    org = await db.get(Org, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    billing_period = _resolve_period(period)
    rows = await _org_breakdown_rows(db, org_id, billing_period)
    breakdown = _breakdown_rows_out(rows)
    third_party_total = _third_party_total(rows)
    allocated_aws = await get_org_allocated_cost(db, org_id, billing_period)

    return AdminOrgUsageOut(
        org_id=str(org.id),
        org_name=org.name,
        license_status=org.license_status,
        billing_period=billing_period,
        breakdown=breakdown,
        allocated_aws_cost_total=float(allocated_aws),
        third_party_cost_total=float(third_party_total),
    )


@admin_router.get("/aws-costs", response_model=AdminAwsCostsOut)
async def admin_get_aws_costs(db: DbDep, period: str = Query("current")) -> AdminAwsCostsOut:
    billing_period = _resolve_period(period)

    pool_rows = (
        await db.execute(
            select(AwsCostPool.pool_category, AwsCostPool.total_cost, AwsCostPool.currency).where(
                AwsCostPool.billing_period == billing_period
            )
        )
    ).all()
    actual_total = sum((r.total_cost for r in pool_rows), Decimal("0"))
    pools = [
        AwsCostPoolOut(
            pool_category=r.pool_category, total_cost=float(r.total_cost), currency=r.currency
        )
        for r in pool_rows
    ]

    latest_version = (
        await db.execute(
            select(CostAllocation.allocation_version)
            .where(CostAllocation.billing_period == billing_period)
            .order_by(CostAllocation.allocation_version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    allocated_org_total = Decimal("0")
    shared_unallocated_total = Decimal("0")
    if latest_version is not None:
        alloc_rows = (
            await db.execute(
                select(CostAllocation.org_id, CostAllocation.allocated_cost).where(
                    CostAllocation.billing_period == billing_period,
                    CostAllocation.allocation_version == latest_version,
                )
            )
        ).all()
        for org_id, allocated_cost in alloc_rows:
            if org_id is None:
                shared_unallocated_total += allocated_cost
            else:
                allocated_org_total += allocated_cost

    return AdminAwsCostsOut(
        billing_period=billing_period,
        actual_aws_total=float(actual_total),
        allocated_org_total=float(allocated_org_total),
        shared_unallocated_total=float(shared_unallocated_total),
        difference=float(actual_total - (allocated_org_total + shared_unallocated_total)),
        pools=pools,
        allocation_version=latest_version,
    )


@admin_router.post("/billing/periods/{period}/close", response_model=BillingPeriodCloseOut)
async def admin_close_billing_period(period: str) -> BillingPeriodCloseOut:
    try:
        result = await close_billing_period(period)
    except PeriodAlreadyLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return BillingPeriodCloseOut(**result)


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite (used in tests) doesn't persist tzinfo on a DateTime(timezone=True)
    column, so a value round-tripped through it — including via
    `db.refresh()` right after we ourselves wrote a UTC-aware one — comes
    back naive even though every value ever written here is UTC. Postgres
    (prod) already returns aware values, so this is a no-op there. Mirrors
    routers/billing.py's identical helper for the identical reason."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _cost_rate_out(rate: CostRate) -> CostRateOut:
    return CostRateOut(
        id=str(rate.id),
        service=rate.service,
        usage_type=rate.usage_type,
        provider=rate.provider,
        unit_cost=float(rate.unit_cost),
        currency=rate.currency,
        effective_from=_as_aware_utc(rate.effective_from).isoformat(),
        effective_to=_as_aware_utc(rate.effective_to).isoformat() if rate.effective_to else None,
        created_at=_as_aware_utc(rate.created_at).isoformat(),
    )


@admin_router.get("/cost-rates", response_model=list[CostRateOut])
async def admin_list_cost_rates(db: DbDep) -> list[CostRateOut]:
    """List every configured `CostRate` — see db/models/cost_rate.py.
    Without at least one rate for a (service, usage_type[, provider]), that
    usage is metered (quantity is always recorded) but never priced:
    `estimated_cost` stays NULL everywhere it's shown."""
    rows = (
        await db.execute(select(CostRate).order_by(CostRate.service, CostRate.usage_type))
    ).scalars().all()
    return [_cost_rate_out(r) for r in rows]


@admin_router.post("/cost-rates", response_model=CostRateOut, status_code=201)
async def admin_create_cost_rate(db: DbDep, payload: CostRateIn) -> CostRateOut:
    """Add a new rate, effective from `payload.effective_from` (default:
    now). Closes out (`effective_to`) any still-open prior rate for the
    same (service, usage_type, provider) key so
    workers/usage_daily_aggregator.py::_unit_cost never sees two
    simultaneously "current" rates for the same key.

    A caller-supplied `effective_from` without a UTC offset (e.g.
    "2026-10-01T00:00:00", easy for a human admin to type) is treated as
    UTC rather than stored naive — every other datetime in this codebase
    is UTC-aware (see deps.py, license workers), and comparing a naive
    value against `datetime.now(UTC)` in `_unit_cost` would otherwise be
    undefined at best (SQLite silently stores it naive; Postgres/asyncpg
    can reject or misinterpret it).
    """
    if payload.effective_from:
        effective_from = datetime.fromisoformat(payload.effective_from)
        if effective_from.tzinfo is None:
            effective_from = effective_from.replace(tzinfo=UTC)
    else:
        effective_from = datetime.now(UTC)

    prior_open = (
        await db.execute(
            select(CostRate).where(
                CostRate.service == payload.service,
                CostRate.usage_type == payload.usage_type,
                CostRate.provider == payload.provider,
                CostRate.effective_to.is_(None),
            )
        )
    ).scalars().all()
    for prior in prior_open:
        prior.effective_to = effective_from

    rate = CostRate(
        service=payload.service,
        usage_type=payload.usage_type,
        provider=payload.provider,
        unit_cost=payload.unit_cost,
        currency=payload.currency,
        effective_from=effective_from,
    )
    db.add(rate)
    await db.commit()
    await db.refresh(rate)
    return _cost_rate_out(rate)


async def verify_internal_service_token(x_admin_token: str | None = Header(None)) -> None:
    """Stricter than `verify_admin_or_session`: internal usage-event
    ingestion is service-to-service, not a dashboard action, so it accepts
    ONLY the shared `X-Admin-Token` — never a session token — and must
    never be reachable anonymously (req §14)."""
    if x_admin_token is None or x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")


@internal_router.post(
    "/usage-events", status_code=201, dependencies=[Depends(verify_internal_service_token)]
)
async def ingest_usage_event(db: DbDep, payload: UsageEventIn) -> dict[str, object]:
    """Kept minimal per the plan: almost every real metering call site in
    this monolith calls core/usage.py::record_usage directly in-process
    (see channels/voice, channels/whatsapp, workers/*); this endpoint
    exists only for the rare caller that can't."""
    from apps.api.core.usage import record_usage

    event = await record_usage(
        db,
        organization_id=UUID(payload.organization_id),
        event_id=payload.event_id,
        service=payload.service,
        usage_type=payload.usage_type,
        quantity=payload.quantity,
        unit=payload.unit,
        source=payload.source,
        provider=payload.provider,
        request_id=payload.request_id,
    )
    await db.commit()
    return {"status": "ok", "recorded": event is not None}
