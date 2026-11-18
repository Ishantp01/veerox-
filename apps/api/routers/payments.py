"""Razorpay plan payments — separate from the manual org-license system
(routers/billing.py). A paid order records a period of plan access in
`razorpay_payments`; it never touches Org.license_*."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from apps.api.config import settings
from apps.api.core.plan_limits import all_usage, get_active_plan
from apps.api.db.models.razorpay_payment import PaymentPlan, RazorpayPayment
from apps.api.deps import (
    CurrentOrg,
    CurrentOrgDep,
    DbDep,
    _org_is_platform_admin_owned,
    require_role,
    verify_platform_admin,
)

logger = logging.getLogger(__name__)

PlatformAdminDep = Annotated[None, Depends(verify_platform_admin)]
OrgAdminDep = Annotated[CurrentOrg, Depends(require_role("admin"))]

router = APIRouter(prefix="/payments", tags=["payments"])

RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"


class PlanIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    price_paise: int = Field(gt=0)
    currency: str = "INR"
    duration_days: int = Field(gt=0)
    is_active: bool = True
    is_addon: bool = False
    max_call_minutes: int | None = Field(default=None, ge=0)
    max_whatsapp_messages: int | None = Field(default=None, ge=0)
    max_team_members: int | None = Field(default=None, ge=0)
    max_voice_campaigns: int | None = Field(default=None, ge=0)
    max_whatsapp_campaigns: int | None = Field(default=None, ge=0)


class PlanPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    price_paise: int | None = Field(default=None, gt=0)
    duration_days: int | None = Field(default=None, gt=0)
    is_active: bool | None = None
    is_addon: bool | None = None
    max_call_minutes: int | None = Field(default=None, ge=0)
    max_whatsapp_messages: int | None = Field(default=None, ge=0)
    max_team_members: int | None = Field(default=None, ge=0)
    max_voice_campaigns: int | None = Field(default=None, ge=0)
    max_whatsapp_campaigns: int | None = Field(default=None, ge=0)


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    name: str
    description: str | None
    price_paise: int
    currency: str
    duration_days: int
    is_active: bool
    is_addon: bool
    max_call_minutes: int | None
    max_whatsapp_messages: int | None
    max_team_members: int | None
    max_voice_campaigns: int | None
    max_whatsapp_campaigns: int | None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    plan_id: UUID
    razorpay_order_id: str
    razorpay_payment_id: str | None
    amount_paise: int
    currency: str
    status: str
    period_start: datetime | None
    period_end: datetime | None
    created_at: datetime


class CheckoutIn(BaseModel):
    plan_id: UUID


class CheckoutOut(BaseModel):
    key_id: str
    order_id: str
    amount: int
    currency: str
    plan_name: str


class VerifyIn(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class UsageItemOut(BaseModel):
    resource: str
    limit: int | None
    used: float


class PaymentStatusOut(BaseModel):
    active: bool
    # True = the UI must send this org to the plans screen before anything else.
    plan_required: bool = False
    plan: PlanOut | None
    period_end: datetime | None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _hmac_ok(message: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def verify_checkout_signature(order_id: str, payment_id: str, signature: str, secret: str) -> bool:
    return _hmac_ok(f"{order_id}|{payment_id}".encode(), signature, secret)


async def _create_razorpay_order(amount: int, currency: str, receipt: str) -> dict[str, Any]:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Razorpay is not configured")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            RAZORPAY_ORDERS_URL,
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            json={"amount": amount, "currency": currency, "receipt": receipt[:40]},
        )
    if resp.status_code >= 400:
        logger.error("razorpay order create failed: %s %s", resp.status_code, resp.text)
        raise HTTPException(status_code=502, detail="Could not create Razorpay order")
    data: dict[str, Any] = resp.json()
    return data


async def _mark_paid(db: DbDep, order_id: str, payment_id: str | None) -> RazorpayPayment | None:
    """Idempotent: verify and the webhook can both fire for one order."""
    result = await db.execute(
        select(RazorpayPayment)
        .where(RazorpayPayment.razorpay_order_id == order_id)
        .with_for_update()
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        return None
    if payment.status == "paid":
        return payment
    plan = await db.get(PaymentPlan, payment.plan_id)
    now = datetime.now(UTC)
    # Stack on top of any still-running paid BASE period for this org.
    latest = await db.execute(
        select(RazorpayPayment.period_end)
        .join(PaymentPlan, PaymentPlan.id == RazorpayPayment.plan_id)
        .where(
            RazorpayPayment.org_id == payment.org_id,
            RazorpayPayment.status == "paid",
            RazorpayPayment.period_end > now,
            PaymentPlan.is_addon.is_(False),
        )
        .order_by(RazorpayPayment.period_end.desc())
        .limit(1)
    )
    running_end = latest.scalar_one_or_none()
    payment.status = "paid"
    payment.razorpay_payment_id = payment_id
    if plan is not None and plan.is_addon:
        # Add-ons never extend access: they live until the running base period
        # ends, which is how plan_limits matches them to that period.
        payment.period_start = now
        payment.period_end = _aware(running_end) if running_end else now
    else:
        # A renewal / new plan takes effect NOW so its usage limits start fresh;
        # the days still left on the running period are kept on top of it.
        base = _aware(running_end) if running_end else now
        payment.period_start = now
        payment.period_end = base + timedelta(days=plan.duration_days if plan else 30)
    await db.commit()
    await db.refresh(payment)
    return payment


# ── Org-facing ────────────────────────────────────────────────────────────


@router.get("/plans", response_model=list[PlanOut])
async def list_available_plans(org: CurrentOrgDep, db: DbDep) -> list[PaymentPlan]:
    result = await db.execute(
        select(PaymentPlan)
        .where(PaymentPlan.is_active.is_(True), PaymentPlan.is_addon.is_(False))
        .order_by(PaymentPlan.price_paise)
    )
    return list(result.scalars())


@router.get("/addons", response_model=list[PlanOut])
async def list_available_addons(org: CurrentOrgDep, db: DbDep) -> list[PaymentPlan]:
    """Top-up packs (e.g. extra call minutes) buyable on top of a running plan."""
    result = await db.execute(
        select(PaymentPlan)
        .where(PaymentPlan.is_active.is_(True), PaymentPlan.is_addon.is_(True))
        .order_by(PaymentPlan.price_paise)
    )
    return list(result.scalars())


@router.get("/status", response_model=PaymentStatusOut)
async def payment_status(org: CurrentOrgDep, db: DbDep) -> PaymentStatusOut:
    result = await db.execute(
        select(RazorpayPayment)
        .join(PaymentPlan, PaymentPlan.id == RazorpayPayment.plan_id)
        .where(
            RazorpayPayment.org_id == org.org_id,
            RazorpayPayment.status == "paid",
            RazorpayPayment.period_end > datetime.now(UTC),
            PaymentPlan.is_addon.is_(False),
        )
        .order_by(RazorpayPayment.period_end.desc())
        .limit(1)
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        required = settings.plan_required_on_login and not await _org_is_platform_admin_owned(
            db, org.org_id
        )
        return PaymentStatusOut(active=False, plan_required=required, plan=None, period_end=None)
    plan = await db.get(PaymentPlan, payment.plan_id)
    return PaymentStatusOut(
        active=True,
        plan=PlanOut.model_validate(plan) if plan else None,
        period_end=payment.period_end,
    )


@router.get("/usage", response_model=list[UsageItemOut])
async def plan_usage(org: CurrentOrgDep, db: DbDep) -> list[UsageItemOut]:
    """Usage vs. limits for the org's active plan (empty if none / owner org)."""
    return [
        UsageItemOut(resource=u.resource, limit=u.limit, used=u.used)
        for u in await all_usage(db, org.org_id)
    ]


@router.get("/history", response_model=list[PaymentOut])
async def payment_history(org: CurrentOrgDep, db: DbDep) -> list[RazorpayPayment]:
    result = await db.execute(
        select(RazorpayPayment)
        .where(RazorpayPayment.org_id == org.org_id)
        .order_by(RazorpayPayment.created_at.desc())
    )
    return list(result.scalars())


@router.post("/checkout", response_model=CheckoutOut)
async def create_checkout(payload: CheckoutIn, org: OrgAdminDep, db: DbDep) -> CheckoutOut:
    # The platform owner's org publishes plans; it never buys them.
    if await _org_is_platform_admin_owned(db, org.org_id):
        raise HTTPException(
            status_code=403, detail="The owner organization does not purchase plans"
        )
    plan = await db.get(PaymentPlan, payload.plan_id)
    if plan is None or not plan.is_active:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.is_addon and await get_active_plan(db, org.org_id) is None:
        raise HTTPException(
            status_code=409, detail="Buy a base plan before purchasing an add-on"
        )
    order = await _create_razorpay_order(plan.price_paise, plan.currency, f"org-{org.org_id}")
    db.add(
        RazorpayPayment(
            org_id=org.org_id,
            plan_id=plan.id,
            razorpay_order_id=order["id"],
            amount_paise=plan.price_paise,
            currency=plan.currency,
            status="created",
        )
    )
    await db.commit()
    return CheckoutOut(
        key_id=settings.razorpay_key_id or "",
        order_id=order["id"],
        amount=plan.price_paise,
        currency=plan.currency,
        plan_name=plan.name,
    )


@router.post("/verify", response_model=PaymentOut)
async def verify_payment(payload: VerifyIn, org: OrgAdminDep, db: DbDep) -> RazorpayPayment:
    if not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Razorpay is not configured")
    if not verify_checkout_signature(
        payload.razorpay_order_id,
        payload.razorpay_payment_id,
        payload.razorpay_signature,
        settings.razorpay_key_secret,
    ):
        raise HTTPException(status_code=400, detail="Invalid payment signature")
    owner = (
        await db.execute(
            select(RazorpayPayment.org_id).where(
                RazorpayPayment.razorpay_order_id == payload.razorpay_order_id
            )
        )
    ).scalar_one_or_none()
    if owner is None or owner != org.org_id:
        raise HTTPException(status_code=404, detail="Order not found")
    payment = await _mark_paid(db, payload.razorpay_order_id, payload.razorpay_payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return payment


@router.post("/webhook")
async def razorpay_webhook(request: Request, db: DbDep) -> dict[str, bool]:
    """Razorpay -> us. No session auth; trusted only via the HMAC signature."""
    secret = settings.razorpay_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook not configured")
    body = await request.body()
    if not _hmac_ok(body, request.headers.get("X-Razorpay-Signature", ""), secret):
        raise HTTPException(status_code=400, detail="Invalid signature")
    event = json.loads(body)
    name = event.get("event")
    entity = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    order_id, payment_id = entity.get("order_id"), entity.get("id")
    if name in ("payment.captured", "order.paid") and order_id:
        await _mark_paid(db, order_id, payment_id)
    elif name == "payment.failed" and order_id:
        result = await db.execute(
            select(RazorpayPayment).where(RazorpayPayment.razorpay_order_id == order_id)
        )
        payment = result.scalar_one_or_none()
        if payment is not None and payment.status == "created":
            payment.status = "failed"
            payment.razorpay_payment_id = payment_id
            await db.commit()
    return {"ok": True}


# ── Platform-admin plan catalog ───────────────────────────────────────────


@router.get("/admin/plans", response_model=list[PlanOut])
async def admin_list_plans(db: DbDep, _admin: PlatformAdminDep) -> list[PaymentPlan]:
    result = await db.execute(select(PaymentPlan).order_by(PaymentPlan.price_paise))
    return list(result.scalars())


@router.post("/admin/plans", response_model=PlanOut, status_code=201)
async def admin_create_plan(payload: PlanIn, db: DbDep, _admin: PlatformAdminDep) -> PaymentPlan:
    plan = PaymentPlan(**payload.model_dump())
    db.add(plan)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Plan code already exists") from None
    await db.refresh(plan)
    return plan


@router.patch("/admin/plans/{plan_id}", response_model=PlanOut)
async def admin_update_plan(
    plan_id: UUID, payload: PlanPatch, db: DbDep, _admin: PlatformAdminDep
) -> PaymentPlan:
    plan = await db.get(PaymentPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(plan, key, value)
    await db.commit()
    await db.refresh(plan)
    return plan


@router.delete("/admin/plans/{plan_id}")
async def admin_delete_plan(plan_id: UUID, db: DbDep, _admin: PlatformAdminDep) -> dict[str, bool]:
    """Delete a plan nobody has paid for. A plan with a paid purchase is part of
    an org's payment history and its usage limits, so it can only be hidden."""
    plan = await db.get(PaymentPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    paid = await db.scalar(
        select(func.count())
        .select_from(RazorpayPayment)
        .where(RazorpayPayment.plan_id == plan_id, RazorpayPayment.status == "paid")
    )
    if paid:
        raise HTTPException(
            status_code=409,
            detail=(
                "This plan has already been bought by a customer, so it can't be deleted. "
                "Use Hide instead - customers who paid keep their plan."
            ),
        )
    # Abandoned / failed checkouts only: they would otherwise block the delete.
    await db.execute(delete(RazorpayPayment).where(RazorpayPayment.plan_id == plan_id))
    await db.delete(plan)
    await db.commit()
    return {"ok": True}
