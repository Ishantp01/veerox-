from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class UsageEventIn(BaseModel):
    """Body for POST /internal/usage-events — see routers/usage.py's
    `verify_internal_service_token` for why this is not reachable
    anonymously or by a dashboard session."""

    organization_id: str
    event_id: str
    service: str
    usage_type: str
    quantity: Decimal
    unit: str
    source: str
    provider: str | None = None
    request_id: str | None = None


class UsageBreakdownRow(BaseModel):
    service: str
    usage_type: str
    # `float`, not `Decimal` — Pydantic v2 serializes Decimal fields to JSON
    # as *strings* (to avoid float-precision surprises on the wire), which
    # would silently break every consumer on the frontend: TS declares
    # these as `number`, and lib/format.ts's formatUsd() explicitly checks
    # `typeof amount === "number"`, so a string would render as $0.00
    # everywhere instead of erroring loudly. Matches every other money/
    # quantity field in this codebase's response schemas (e.g.
    # schemas/lead.py's deal_value, schemas/reports.py's usd_spend) — only
    # UsageEventIn (a request body, never rendered) stays Decimal for
    # ingestion precision.
    total_quantity: float
    estimated_cost: float | None = None


class OrgUsageOut(BaseModel):
    org_id: str
    billing_period: str
    breakdown: list[UsageBreakdownRow]
    third_party_cost_total: float
    # Always labeled this way, never "AWS invoice" — the shared-infra
    # component of an org's estimate is a documented proxy-metric share, not
    # an exact per-tenant AWS bill (req §10/§12).
    estimated_infrastructure_usage: float
    platform_fee: float
    estimated_total_charge: float
    calculation_status: str


class BillingHistoryRow(BaseModel):
    billing_period: str
    usage_charge: float
    platform_fee: float
    total_amount: float
    status: str


class OrgBillingEstimateOut(BaseModel):
    org_id: str
    billing_period: str
    usage_charge_estimate: float
    estimated_infrastructure_usage: float
    platform_fee: float
    estimated_total_charge: float
    history: list[BillingHistoryRow]


class AdminOrgUsageOut(BaseModel):
    org_id: str
    org_name: str
    license_status: str
    billing_period: str
    breakdown: list[UsageBreakdownRow]
    allocated_aws_cost_total: float
    third_party_cost_total: float


class AwsCostPoolOut(BaseModel):
    pool_category: str
    total_cost: float
    currency: str


class AdminAwsCostsOut(BaseModel):
    billing_period: str
    actual_aws_total: float
    allocated_org_total: float
    shared_unallocated_total: float
    difference: float
    pools: list[AwsCostPoolOut]
    allocation_version: int | None = None


class BillingPeriodCloseOut(BaseModel):
    billing_period: str
    invoiced_orgs: int


class CostRateIn(BaseModel):
    """Body for POST /admin/cost-rates — see db/models/cost_rate.py."""

    service: str
    usage_type: str
    provider: str | None = None
    unit_cost: Decimal
    currency: str = "USD"
    # Defaults to "now" server-side if omitted (see routers/usage.py).
    effective_from: str | None = None


class CostRateOut(BaseModel):
    id: str
    service: str
    usage_type: str
    provider: str | None
    unit_cost: float
    currency: str
    effective_from: str
    effective_to: str | None
    created_at: str
