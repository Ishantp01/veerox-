"""Imports actual AWS infrastructure spend via the Cost Explorer API into
`AwsCostPool` rows (req §9).

Disabled by default (`settings.aws_cost_import_enabled = False`) and a
complete no-op until an operator both flips that flag and provides AWS
credentials with Cost Explorer read access (`ce:GetCostAndUsage`) via the
standard boto3 credential chain (env vars, an attached IAM role, or a
profile) — nothing AWS-specific is invented here, and no credential is ever
read from or written to this codebase. `boto3` is imported lazily inside
`_fetch_cost_and_usage` so the rest of the app works unmodified in any
environment that hasn't installed it yet.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apps.api.config import settings
from apps.api.db.models.aws_cost_pool import AWS_COST_POOL_CATEGORIES, AwsCostPool
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import record_error
from apps.api.workers.usage_monthly_aggregator import current_billing_period

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECS = 24 * 3600

# AWS service name (as Cost Explorer reports it) -> documented pool category
# (req §9's compute/database/storage/network/serverless/queue/logging/misc).
# Anything not listed here falls into "misc" rather than being dropped, so
# the sum of pool rows for a period always reconciles to the Cost Explorer
# total.
_SERVICE_CATEGORY_MAP: dict[str, str] = {
    "Amazon Elastic Compute Cloud - Compute": "compute",
    "EC2 - Other": "compute",
    "Amazon Elastic Container Service": "compute",
    "Amazon Relational Database Service": "database",
    "Amazon DynamoDB": "database",
    "Amazon Simple Storage Service": "storage",
    "Amazon Elastic Block Store": "storage",
    "Amazon CloudFront": "network",
    "Amazon Virtual Private Cloud": "network",
    "AWS Data Transfer": "network",
    "AWS Lambda": "serverless",
    "Amazon API Gateway": "serverless",
    "Amazon Simple Queue Service": "queue",
    "Amazon MQ": "queue",
    "AmazonCloudWatch": "logging",
    "AWS CloudTrail": "logging",
}


def categorize_service(aws_service: str) -> str:
    category = _SERVICE_CATEGORY_MAP.get(aws_service, "misc")
    assert category in AWS_COST_POOL_CATEGORIES
    return category


def _period_bounds(billing_period: str) -> tuple[str, str]:
    """Cost Explorer wants an ISO `Start`/`End` (End exclusive)."""
    year, month = (int(p) for p in billing_period.split("-"))
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


def _fetch_cost_and_usage(billing_period: str) -> list[dict[str, Any]]:
    """Synchronous boto3 call (Cost Explorer has no async client) — run via
    `asyncio.to_thread` by the caller. Returns
    `[{"aws_service": ..., "total_cost": Decimal, "currency": ...}, ...]`.
    """
    import boto3  # local import — optional dependency, only needed when enabled

    start, end = _period_bounds(billing_period)
    client = boto3.client("ce")
    response = client.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    results: list[dict[str, Any]] = []
    for time_period in response.get("ResultsByTime", []):
        for group in time_period.get("Groups", []):
            service_name = group["Keys"][0]
            amount_block = group["Metrics"]["UnblendedCost"]
            results.append(
                {
                    "aws_service": service_name,
                    "total_cost": Decimal(amount_block["Amount"]),
                    "currency": amount_block["Unit"],
                }
            )
    return results


async def import_aws_costs(billing_period: str | None = None) -> int:
    """Import actual AWS cost for `billing_period` (default: the current
    open period) into `AwsCostPool`. No-ops and returns 0 if disabled or
    unconfigured — never raises for "not set up", only for a real API
    failure once enabled."""
    if not settings.aws_cost_import_enabled:
        return 0
    if not settings.aws_account_id:
        logger.warning("aws_cost_importer_missing_account_id")
        return 0

    period = billing_period or current_billing_period()
    try:
        rows = await asyncio.to_thread(_fetch_cost_and_usage, period)
    except ImportError:
        logger.warning("aws_cost_importer_boto3_not_installed")
        return 0

    written = 0
    async with AsyncSessionLocal() as db:
        for row in rows:
            category = categorize_service(row["aws_service"])
            existing = (
                await db.execute(
                    select(AwsCostPool).where(
                        AwsCostPool.billing_period == period,
                        AwsCostPool.aws_account_id == settings.aws_account_id,
                        AwsCostPool.aws_service == row["aws_service"],
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.total_cost = row["total_cost"]
                existing.currency = row["currency"]
                existing.pool_category = category
            else:
                try:
                    async with db.begin_nested():
                        db.add(
                            AwsCostPool(
                                billing_period=period,
                                aws_account_id=settings.aws_account_id,
                                aws_service=row["aws_service"],
                                pool_category=category,
                                total_cost=row["total_cost"],
                                currency=row["currency"],
                                source="cost_explorer",
                            )
                        )
                        await db.flush()
                except IntegrityError:
                    continue
            written += 1
        await db.commit()
    logger.info("aws_cost_importer_imported", period=period, rows=written)
    return written


async def run_aws_cost_importer() -> None:
    """The worker's main loop — a cheap no-op tick when the feature flag is
    off (the common case today), matching every other worker's
    always-running lifespan task rather than being conditionally started."""
    while True:
        try:
            await import_aws_costs()
        except Exception:  # noqa: BLE001
            logger.exception("aws_cost_importer_tick_failed")
            await record_error()
        await asyncio.sleep(_POLL_INTERVAL_SECS)
