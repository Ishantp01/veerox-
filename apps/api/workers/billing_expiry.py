"""Enforces plan expiry for one-time-Order billing (see routers/billing.py) —
there's no Razorpay auto-charge to fail here, so nothing else in the system
ever revisits an org after its paid period ends. Without this loop, an org
that made a single successful payment would stay `billing_status: active`
forever.

Each tick:
1. Downgrades any org whose latest paid period has ended to `past_due`.
2. Logs a structured warning for orgs approaching expiry
   (`_REMINDER_WINDOW`) so it shows up in ops/Sentry — no outbound
   email/WhatsApp is sent to the org owner yet (no email provider is
   configured in this project); the billing page's "Access until X" copy is
   the customer-facing half of this today.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from apps.api.db.models.billing_payment import BillingPayment
from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECS = 3600
_REMINDER_WINDOW = timedelta(days=3)


async def _latest_paid_payment_by_org() -> dict:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BillingPayment)
            .where(BillingPayment.status == "paid")
            .order_by(BillingPayment.org_id, BillingPayment.period_end.desc())
        )
        latest: dict = {}
        for payment in result.scalars().all():
            latest.setdefault(payment.org_id, payment)
        return latest


async def _tick() -> None:
    now = datetime.now(UTC)
    latest_by_org = await _latest_paid_payment_by_org()

    async with AsyncSessionLocal() as db:
        active_orgs = (
            await db.execute(select(Org).where(Org.billing_status == "active"))
        ).scalars().all()

        downgraded = 0
        for org in active_orgs:
            payment = latest_by_org.get(org.id)
            if payment is None or payment.period_end is None:
                # No paid Order on record at all (e.g. free plan, or admin
                # manually assigned a plan) — nothing to expire here.
                continue

            if payment.period_end <= now:
                org.billing_status = "past_due"
                downgraded += 1
                logger.info(
                    "billing_expiry_downgraded_org",
                    org_id=str(org.id),
                    period_end=payment.period_end.isoformat(),
                )
            elif payment.period_end - now <= _REMINDER_WINDOW:
                logger.warning(
                    "billing_expiry_approaching",
                    org_id=str(org.id),
                    period_end=payment.period_end.isoformat(),
                )

        if downgraded:
            await db.commit()


async def run_billing_expiry_checker() -> None:
    """Main loop — runs for the lifetime of the app process."""
    while True:
        try:
            await _tick()
        except Exception:  # noqa: BLE001
            logger.exception("billing_expiry_tick_failed")
            await record_error()
        await asyncio.sleep(_POLL_INTERVAL_SECS)
