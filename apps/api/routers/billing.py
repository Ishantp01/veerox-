"""Razorpay billing: one-time Orders (not Subscriptions) for plan purchases,
plus a webhook for server-side payment confirmation.

Razorpay Subscriptions require the "Subscriptions" product to be separately
activated on the merchant account (a dashboard-side provisioning step,
independent of having API keys) before subscription.* events even appear as
webhook options — that gate blocked local/test-mode setup, so billing here
is done with plain Orders instead: each plan purchase is a one-time payment
that grants that plan's credits, tracked in `billing_payments`.

Access is credit-based, not time-based: a payment is a *recharge*, and it
lasts until the org has used up the plan's included call minutes/messages —
there is no 30-day window and nothing resets on the calendar (see
apps/api/core/usage.py). `BillingPayment.period_start` records when a
recharge landed; `period_end` is left null, since nothing expires on a
timer. There is no provider-side auto-renew either; the frontend prompts
the org to check out again once its credits are exhausted.

The webhook route has no session/admin auth — Razorpay authenticates itself
by signing the payload (verified via `client.utility.verify_webhook_signature`
against `settings.razorpay_webhook_secret`). Every event is written to
`billing_events` before processing so redelivered webhooks are safe no-ops
(idempotent on `provider_event_id`, taken from the `X-Razorpay-Event-Id`
header Razorpay sends with each delivery). Only `payment.captured` and
`payment.failed` are handled — both are core Payments-category events
available on every Razorpay account, unlike subscription.* events.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import time
from datetime import UTC, datetime
from typing import Annotated, Any, cast
from uuid import UUID

import openpyxl
import razorpay
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from razorpay.errors import SignatureVerificationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.core.sessions import invalidate_user_sessions
from apps.api.core.usage import get_credit_usage
from apps.api.db.models.account_user import AccountUser
from apps.api.db.models.billing_event import BillingEvent
from apps.api.db.models.billing_payment import BillingPayment
from apps.api.db.models.org import Org
from apps.api.db.models.org_membership import OrgMembership
from apps.api.db.models.plan import RESOURCE_TYPE_LIMIT_KEY, Plan
from apps.api.db.models.platform_settings import PlatformSettings
from apps.api.deps import (
    CurrentOrg,
    CurrentOrgDep,
    DbDep,
    RedisDep,
    effective_limits,
    require_role,
    verify_platform_admin,
)
from apps.api.schemas.billing import (
    BillingPaymentOut,
    BillingStatusOut,
    BillingUsageOut,
    CheckoutSessionIn,
    CheckoutSessionOut,
    OrgAdminOut,
    OrgPaymentAdminOut,
    PlanAdminOut,
    PlanCreateIn,
    PlatformSettingsOut,
    PlatformSettingsUpdateIn,
    RegenerateAdminTokenOut,
    PlanOut,
    PlanUpdateIn,
    SocialLinksOut,
    UsageMetricOut,
    VerifyPaymentIn,
)

# Plan definitions are a platform-wide catalog shared by every org, not an
# org-scoped resource. Gated by X-Admin-Token or `AccountUser.is_superuser`
# specifically (verify_platform_admin) rather than the OR-guard admin.py
# uses — that guard accepts ANY org's valid session, which would let any
# self-signed-up customer reprice the whole platform's plan catalog.
PlatformAdminDep = Annotated[None, Depends(verify_platform_admin)]

# Checkout/payment are org-level actions ("touch billing") — restricted to
# admin so a plain member can view billing status (CurrentOrgDep, read
# only) but can't start a new charge or activate a plan.
ManagerDep = Annotated[CurrentOrg, Depends(require_role("admin"))]

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

_RAZORPAY_API_ERRORS = (
    razorpay.errors.BadRequestError,
    razorpay.errors.GatewayError,
    razorpay.errors.ServerError,
)


def _razorpay_client() -> razorpay.Client:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))


@router.get("/usage", response_model=BillingUsageOut)
async def get_billing_usage(org: CurrentOrgDep, db: DbDep) -> BillingUsageOut:
    org_result = await db.execute(select(Org).where(Org.id == org.org_id))
    target_org = org_result.scalar_one()

    plan: Plan | None = None
    if target_org.plan_id is not None:
        plan_result = await db.execute(select(Plan).where(Plan.id == target_org.plan_id))
        plan = plan_result.scalar_one_or_none()
    limits = effective_limits(target_org, plan)

    usage = await get_credit_usage(db, org.org_id)
    return BillingUsageOut(
        period_start=usage.period_start.isoformat(),
        campaigns=UsageMetricOut(
            used=usage.campaigns, limit=limits.get("max_campaigns")
        ),
        call_minutes=UsageMetricOut(
            used=usage.call_minutes, limit=limits.get("max_call_minutes")
        ),
        whatsapp_messages=UsageMetricOut(
            used=usage.whatsapp_messages, limit=limits.get("max_whatsapp_messages")
        ),
    )


def _plan_admin_out(plan: Plan) -> PlanAdminOut:
    return PlanAdminOut(
        code=plan.code,
        name=plan.name,
        price_cents=plan.price_cents,
        limits=plan.limits,
        is_active=plan.is_active,
        resource_type=plan.resource_type,
    )


@router.get("/orgs", response_model=list[OrgAdminOut])
async def list_orgs(db: DbDep, _admin: PlatformAdminDep) -> list[OrgAdminOut]:
    """Platform-wide org directory — every org on the platform, not just the
    caller's own (see PlatformAdminDep). A regular user never gets this view:
    every non-admin route is scoped to the caller's own org.
    """
    result = await db.execute(select(Org).order_by(Org.created_at))
    orgs = result.scalars().all()

    plan_ids = {org.plan_id for org in orgs if org.plan_id is not None}
    plans_by_id: dict[UUID, Plan] = {}
    if plan_ids:
        plan_result = await db.execute(select(Plan).where(Plan.id.in_(plan_ids)))
        plans_by_id = {plan.id: plan for plan in plan_result.scalars().all()}

    out: list[OrgAdminOut] = []
    for org in orgs:
        seat_count_result = await db.execute(
            select(func.count()).select_from(OrgMembership).where(OrgMembership.org_id == org.id)
        )
        seat_count = seat_count_result.scalar_one()

        admin_result = await db.execute(
            select(AccountUser.email)
            .join(OrgMembership, OrgMembership.account_user_id == AccountUser.id)
            .where(OrgMembership.org_id == org.id, OrgMembership.role == "admin")
            .order_by(OrgMembership.created_at)
            .limit(1)
        )
        admin_email = admin_result.scalar_one_or_none()

        plan = plans_by_id.get(org.plan_id) if org.plan_id else None
        out.append(
            OrgAdminOut(
                id=str(org.id),
                name=org.name,
                plan_code=plan.code if plan else None,
                billing_status=org.billing_status,
                seat_count=seat_count,
                admin_email=admin_email,
                created_at=org.created_at.isoformat(),
            )
        )
    return out


@router.get("/orgs.xlsx")
async def export_orgs_xlsx(db: DbDep, _admin: PlatformAdminDep) -> StreamingResponse:
    """Same data as GET /billing/orgs, as a downloadable .xlsx workbook —
    backs the Organizations page's export button."""
    orgs = await list_orgs(db, _admin)

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Organizations"
    sheet.append(["organization", "admin_email", "plan", "billing_status", "team_members", "created_at"])
    for org in orgs:
        sheet.append(
            [
                org.name,
                org.admin_email or "",
                org.plan_code or "No plan",
                org.billing_status,
                org.seat_count,
                org.created_at,
            ]
        )

    buf = io.BytesIO()
    workbook.save(buf)
    buf.seek(0)

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="organizations-{stamp}.xlsx"'},
    )


async def _load_org_payments(
    db: AsyncSession, org_id: UUID
) -> list[tuple[BillingPayment, Plan | None]]:
    """Every BillingPayment row for an org (created/failed orders included,
    not just paid ones), newest first. Shared by the self-service and
    platform-admin payment-history endpoints below.
    """
    result = await db.execute(
        select(BillingPayment, Plan)
        .outerjoin(Plan, BillingPayment.plan_id == Plan.id)
        .where(BillingPayment.org_id == org_id)
        .order_by(BillingPayment.created_at.desc())
    )
    return list(result.all())


@router.get("/orgs/{org_id}/payments", response_model=list[OrgPaymentAdminOut])
async def list_org_payments(
    org_id: UUID, db: DbDep, _admin: PlatformAdminDep
) -> list[OrgPaymentAdminOut]:
    """Full payment history for one org, for the platform admin — backs the
    "Payment history" action on the Organizations page.
    """
    rows = await _load_org_payments(db, org_id)
    return [
        OrgPaymentAdminOut(
            id=str(payment.id),
            provider=payment.provider,
            plan_code=plan.code if plan else None,
            plan_name=plan.name if plan else None,
            amount_cents=payment.amount_cents,
            status=payment.status,
            period_start=payment.period_start.isoformat() if payment.period_start else None,
            created_at=payment.created_at.isoformat(),
        )
        for payment, plan in rows
    ]


@router.post("/orgs/{org_id}/regenerate-admin-token", response_model=RegenerateAdminTokenOut)
async def regenerate_admin_token(
    org_id: UUID, db: DbDep, redis: RedisDep, _admin: PlatformAdminDep
) -> RegenerateAdminTokenOut:
    """For when an admin-issued login token is lost — there's no password
    reset to fall back on (see routers/auth.py), and the original token
    can't be recovered since only its hash is stored. Issues a fresh token
    for the org's earliest admin account and immediately invalidates their
    existing sessions, since the old token may be compromised too.
    """
    admin_result = await db.execute(
        select(AccountUser)
        .join(OrgMembership, OrgMembership.account_user_id == AccountUser.id)
        .where(OrgMembership.org_id == org_id, OrgMembership.role == "admin")
        .order_by(OrgMembership.created_at)
        .limit(1)
    )
    admin = admin_result.scalar_one_or_none()
    if admin is None:
        raise HTTPException(status_code=404, detail="Org has no admin account")

    login_token = generate_login_token()
    admin.token_hash = hash_token(login_token)
    await db.commit()
    await invalidate_user_sessions(redis, admin.id)

    return RegenerateAdminTokenOut(
        account_user_id=str(admin.id), email=admin.email, login_token=login_token
    )


@router.get("/plans", response_model=list[PlanAdminOut])
async def list_plans(db: DbDep, _admin: PlatformAdminDep) -> list[PlanAdminOut]:
    result = await db.execute(select(Plan).order_by(Plan.price_cents))
    return [_plan_admin_out(plan) for plan in result.scalars().all()]


@router.post("/plans", response_model=PlanAdminOut, status_code=201)
async def create_plan(payload: PlanCreateIn, db: DbDep, _admin: PlatformAdminDep) -> PlanAdminOut:
    existing = await db.execute(select(Plan).where(Plan.code == payload.code))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="A plan with that code already exists")

    # Plans have no Razorpay-side counterpart — Orders are created ad hoc at
    # checkout time using price_cents directly, so paid plans need
    # nothing beyond a price to become checkout-able.
    plan = Plan(
        code=payload.code,
        name=payload.name,
        price_cents=payload.price_cents,
        limits=payload.limits,
        is_active=payload.is_active,
        resource_type=payload.resource_type,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return _plan_admin_out(plan)


@router.patch("/plans/{code}", response_model=PlanAdminOut)
async def update_plan(
    code: str, payload: PlanUpdateIn, db: DbDep, _admin: PlatformAdminDep
) -> PlanAdminOut:
    result = await db.execute(select(Plan).where(Plan.code == code))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(plan, field, value)
    await db.commit()
    await db.refresh(plan)
    return _plan_admin_out(plan)


@router.delete("/plans/{code}", status_code=204)
async def delete_plan(code: str, db: DbDep, _admin: PlatformAdminDep) -> None:
    result = await db.execute(select(Plan).where(Plan.code == code))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    in_use_result = await db.execute(
        select(func.count()).select_from(Org).where(Org.plan_id == plan.id)
    )
    if in_use_result.scalar_one() > 0:
        raise HTTPException(
            status_code=400,
            detail="Plan is assigned to at least one org — reassign them before deleting",
        )

    await db.delete(plan)
    await db.commit()


async def _get_platform_settings(db: DbDep) -> PlatformSettings:
    record = await db.get(PlatformSettings, 1)
    if record is None:
        # Defensive fallback in case the seed row from the migration is
        # somehow missing — creates it on first read instead of 500ing.
        record = PlatformSettings(id=1, help_desk_script=None, social_links={})
        db.add(record)
        await db.commit()
        await db.refresh(record)
    return record


@router.get("/platform-settings", response_model=PlatformSettingsOut)
async def get_platform_settings(db: DbDep, _admin: PlatformAdminDep) -> PlatformSettingsOut:
    """Platform-wide help-desk script + social links, for the admin editor
    on the Billing page (visible next to the Plan catalog)."""
    record = await _get_platform_settings(db)
    return PlatformSettingsOut(
        help_desk_script=record.help_desk_script, social_links=record.social_links
    )


@router.patch("/platform-settings", response_model=PlatformSettingsOut)
async def update_platform_settings(
    payload: PlatformSettingsUpdateIn, db: DbDep, _admin: PlatformAdminDep
) -> PlatformSettingsOut:
    record = await _get_platform_settings(db)
    fields = payload.model_dump(exclude_unset=True)
    if "help_desk_script" in fields:
        value = fields["help_desk_script"]
        record.help_desk_script = value.strip() if value and value.strip() else None
    if "social_links" in fields:
        record.social_links = {k: v.strip() for k, v in (fields["social_links"] or {}).items() if v and v.strip()}
    await db.commit()
    await db.refresh(record)
    return PlatformSettingsOut(
        help_desk_script=record.help_desk_script, social_links=record.social_links
    )


@router.get("/social-links", response_model=SocialLinksOut)
async def get_social_links(org: CurrentOrgDep, db: DbDep) -> SocialLinksOut:
    """Read-only social links for client dashboards — any authenticated org
    member (not just admins) can see these."""
    _ = org  # CurrentOrgDep only used to require *some* valid session
    record = await _get_platform_settings(db)
    return SocialLinksOut(social_links=record.social_links)


@router.get("/available-plans", response_model=list[PlanOut])
async def list_available_plans(org: CurrentOrgDep, db: DbDep) -> list[PlanOut]:
    """The real, admin-managed plan catalog — active plans only, no
    Razorpay-internal fields (that's what PlanAdminOut/list_plans is for).
    Any authenticated org member can read this; it's what the Billing page's
    "Choose plan" cards render, so it must reflect whatever the platform
    admin has actually configured rather than a hardcoded frontend list.
    """
    _ = org  # CurrentOrgDep only used to require *some* valid session
    result = await db.execute(
        select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_cents)
    )
    return [
        PlanOut(
            code=plan.code,
            name=plan.name,
            price_cents=plan.price_cents,
            limits=plan.limits,
            resource_type=plan.resource_type,
        )
        for plan in result.scalars().all()
    ]


@router.get("/status", response_model=BillingStatusOut)
async def get_billing_status(org: CurrentOrgDep, db: DbDep) -> BillingStatusOut:
    # Org + Plan + seat count in one round trip (a correlated scalar
    # subquery for the count) instead of three separate queries — DB here
    # is a remote Neon instance (see .env), so every extra round trip this
    # endpoint fires (loaded on every dashboard page) is real latency.
    # Matches routers/team.py's max_seats enforcement: the org owner
    # (provisioned via /auth/provision-org, invited_by_id is null) doesn't
    # count as a used seat — only teammates actually invited do. Counting
    # the owner here would make this "used" figure disagree with what
    # actually blocks a new invite.
    seat_count_subq = (
        select(func.count())
        .select_from(OrgMembership)
        .where(OrgMembership.org_id == Org.id, OrgMembership.invited_by_id.is_not(None))
        .correlate(Org)
        .scalar_subquery()
    )
    org_result = await db.execute(
        select(Org, Plan, seat_count_subq.label("seat_count"))
        .outerjoin(Plan, Org.plan_id == Plan.id)
        .where(Org.id == org.org_id)
    )
    target_org, plan, seat_count = org_result.one()

    plan_out: PlanOut | None = None
    if plan is not None:
        plan_out = PlanOut(
            code=plan.code,
            name=plan.name,
            price_cents=plan.price_cents,
            limits=effective_limits(target_org, plan),
            resource_type=plan.resource_type,
        )

    # Ordered by period_start (when the recharge landed), not period_end —
    # period_end is null now that credits don't expire on a timer.
    payment_result = await db.execute(
        select(BillingPayment)
        .where(BillingPayment.org_id == org.org_id, BillingPayment.status == "paid")
        .order_by(BillingPayment.period_start.desc())
        .limit(1)
    )
    latest_payment = payment_result.scalar_one_or_none()

    return BillingStatusOut(
        billing_status=target_org.billing_status,
        plan=plan_out,
        seat_count=seat_count,
        last_recharge_at=(
            latest_payment.period_start.isoformat()
            if latest_payment and latest_payment.period_start
            else None
        ),
        free_plan_claimed=target_org.free_plan_claimed_at is not None,
    )


@router.get("/payments", response_model=list[BillingPaymentOut])
async def list_billing_payments(org: CurrentOrgDep, db: DbDep) -> list[BillingPaymentOut]:
    """Self-service billing history for the caller's own org — any
    authenticated member can view (read-only, same as /billing/status);
    only checkout/verify are restricted to admins. Backs the "Billing
    history" section on the org's own Billing page.
    """
    rows = await _load_org_payments(db, org.org_id)
    return [
        BillingPaymentOut(
            id=str(payment.id),
            provider=payment.provider,
            plan_code=plan.code if plan else None,
            plan_name=plan.name if plan else None,
            amount_cents=payment.amount_cents,
            status=payment.status,
            period_start=payment.period_start.isoformat() if payment.period_start else None,
            created_at=payment.created_at.isoformat(),
        )
        for payment, plan in rows
    ]


@router.post("/checkout-session", response_model=CheckoutSessionOut)
async def create_checkout_session(
    payload: CheckoutSessionIn, org: ManagerDep, db: DbDep
) -> CheckoutSessionOut:
    plan_result = await db.execute(select(Plan).where(Plan.code == payload.plan_code))
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=400, detail="Unknown plan")

    if plan.price_cents == 0:
        # Free plan — no payment to collect, so there's nothing for Razorpay
        # to do. Assign it directly and send the caller straight back to
        # their own success_url (no external redirect needed).
        org_result = await db.execute(select(Org).where(Org.id == org.org_id))
        target_org = org_result.scalar_one()
        if target_org.free_plan_claimed_at is not None:
            # One-time only, per org: once any free plan has been granted,
            # it can never be selected or renewed again, whether the org is
            # still on it, out of credit, or has since moved to a paid plan.
            raise HTTPException(status_code=409, detail="Free plan has already been used")
        if plan.resource_type is None:
            target_org.plan_id = plan.id
            target_org.billing_status = "active"
            target_org.plan_started_at = datetime.now(UTC)
            target_org.resource_limits = dict(plan.limits)
        else:
            await _apply_resource_recharge(db, target_org, plan)
            target_org.billing_status = "active"
        target_org.free_plan_claimed_at = datetime.now(UTC)
        await db.commit()
        return CheckoutSessionOut(checkout_url=payload.success_url)

    client = _razorpay_client()

    # Receipt just needs to be unique per order (org_id/plan_code already
    # travel in `notes` for lookup) — Razorpay caps it at 40 chars, too
    # short to fit a UUID plus plan code plus timestamp.
    receipt = f"{org.org_id.hex[:20]}{int(time.time())}"

    def _create() -> dict[str, Any]:
        return cast(
            "dict[str, Any]",
            client.order.create(
                {
                    "amount": plan.price_cents,
                    "currency": "INR",
                    "receipt": receipt,
                    "notes": {"org_id": str(org.org_id), "plan_code": plan.code},
                }
            ),
        )

    try:
        order = await asyncio.to_thread(_create)
    except _RAZORPAY_API_ERRORS as exc:
        logger.error("razorpay_order_create_failed", org_id=str(org.org_id), error=str(exc))
        raise HTTPException(status_code=502, detail="Failed to create checkout session") from exc

    db.add(
        BillingPayment(
            org_id=org.org_id,
            provider="razorpay",
            provider_order_id=order["id"],
            plan_id=plan.id,
            amount_cents=plan.price_cents,
            status="created",
        )
    )
    await db.commit()

    return CheckoutSessionOut(
        order_id=order["id"],
        amount_cents=plan.price_cents,
        razorpay_key_id=settings.razorpay_key_id,
    )


async def _apply_resource_recharge(
    db: AsyncSession, target_org: Org, purchased_plan: Plan
) -> None:
    """Tops up exactly the one resource `purchased_plan.resource_type` names
    on `target_org.resource_limits`, additively — leaving every other
    resource, `plan_id`, and `plan_started_at` untouched. Not moving
    `plan_started_at` is what makes this additive rather than a reset:
    usage already counted since the org's last full reset keeps counting
    against the other (unchanged) limits, while this one limit goes up by
    exactly the recharged amount (core/usage.py).

    The first time an org's `resource_limits` is touched, it's seeded from
    the org's *current* plan (not the purchased recharge SKU) so resources
    never recharged individually keep reading whatever the base plan
    already granted them. An org with no current plan at all (its very
    first purchase is a partial recharge) is seeded at 0 across every
    resource instead — a missing key reads as *unlimited* elsewhere
    (`effective_limits`/`is_over_plan_limit`), and a fresh org whose only
    purchase was e.g. call minutes must not get free-unlimited WhatsApp/
    seats/campaigns as a side effect of never having bought them.
    """
    key = RESOURCE_TYPE_LIMIT_KEY[purchased_plan.resource_type]
    amount = purchased_plan.limits.get(purchased_plan.resource_type, 0)

    if target_org.resource_limits is None:
        base_plan: Plan | None = None
        if target_org.plan_id is not None:
            base_plan_result = await db.execute(
                select(Plan).where(Plan.id == target_org.plan_id)
            )
            base_plan = base_plan_result.scalar_one_or_none()
        target_org.resource_limits = (
            dict(base_plan.limits)
            if base_plan is not None
            else dict.fromkeys(set(RESOURCE_TYPE_LIMIT_KEY.values()), 0)
        )

    # Reassign a new dict — plain JSON columns don't track in-place mutation.
    new_limits = dict(target_org.resource_limits)
    new_limits[key] = new_limits.get(key, 0) + amount
    target_org.resource_limits = new_limits


async def _activate_paid_payment(
    db: AsyncSession, payment: BillingPayment, razorpay_payment_id: str
) -> None:
    """Marks a BillingPayment paid and applies its plan to the org.
    Idempotent — both verify_payment (client-side) and the webhook
    (server-side) can call this for the same order without double-applying.

    A full-subscription plan (Plan.resource_type is None) replaces the org's
    plan_id and resets plan_started_at, same as always — resetting
    plan_started_at is what restores credit, since usage is counted from
    that timestamp forward (core/usage.py), so moving it to now zeroes the
    org's consumed call minutes/messages/etc. across every resource at once.

    A single-resource recharge (Plan.resource_type set) instead only tops up
    that one resource via `_apply_resource_recharge` — plan_id and
    plan_started_at are deliberately left untouched, since resetting them
    would reset every other resource's usage window too.
    """
    if payment.status == "paid":
        return

    payment.status = "paid"
    payment.provider_payment_id = razorpay_payment_id
    now = datetime.now(UTC)
    payment.period_start = now
    # Left null deliberately: credits don't expire on a timer, so there is
    # no end date to record. Org.plan_started_at (set below, for a full
    # plan) is what usage is measured from.
    payment.period_end = None

    org_result = await db.execute(select(Org).where(Org.id == payment.org_id))
    target_org = org_result.scalar_one_or_none()
    if target_org is None:
        return

    plan_result = await db.execute(select(Plan).where(Plan.id == payment.plan_id))
    purchased_plan = plan_result.scalar_one_or_none()
    if purchased_plan is None:
        logger.warning(
            "billing_payment_activation_missing_plan",
            org_id=str(payment.org_id),
            plan_id=str(payment.plan_id),
        )
        return

    if purchased_plan.resource_type is None:
        target_org.plan_id = purchased_plan.id
        target_org.billing_status = "active"
        target_org.plan_started_at = now
        target_org.resource_limits = dict(purchased_plan.limits)
    else:
        await _apply_resource_recharge(db, target_org, purchased_plan)
        target_org.billing_status = "active"


@router.post("/verify-payment", status_code=204)
async def verify_payment(payload: VerifyPaymentIn, org: ManagerDep, db: DbDep) -> None:
    """Confirms a Razorpay Checkout payment client-side, without depending
    on a publicly-reachable webhook URL — useful for local/test-mode
    development. `client.utility.verify_payment_signature` proves the
    order_id/payment_id pair was genuinely signed by Razorpay with our key
    secret; only then does the org's plan activate. Production should still
    register the real webhook (see /billing/webhook) as a second,
    provider-initiated confirmation path.
    """
    client = _razorpay_client()

    payment_result = await db.execute(
        select(BillingPayment).where(
            BillingPayment.org_id == org.org_id,
            BillingPayment.provider_order_id == payload.razorpay_order_id,
        )
    )
    payment = payment_result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="No matching order for this org")

    try:
        client.utility.verify_payment_signature(
            {
                "razorpay_order_id": payload.razorpay_order_id,
                "razorpay_payment_id": payload.razorpay_payment_id,
                "razorpay_signature": payload.razorpay_signature,
            }
        )
    except SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail="Payment verification failed") from exc

    await _activate_paid_payment(db, payment, payload.razorpay_payment_id)
    await db.commit()


@router.post("/webhook", status_code=204)
async def razorpay_webhook(
    request: Request,
    db: DbDep,
    x_razorpay_signature: str | None = Header(None, alias="X-Razorpay-Signature"),
    x_razorpay_event_id: str | None = Header(None, alias="X-Razorpay-Event-Id"),
) -> None:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    if not settings.razorpay_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret is not configured")
    body = await request.body()

    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    try:
        client.utility.verify_webhook_signature(
            body.decode("utf-8"), x_razorpay_signature or "", settings.razorpay_webhook_secret
        )
    except SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook signature") from exc

    event = await request.json()
    # Razorpay sends X-Razorpay-Event-Id with each delivery; fall back to a
    # content hash for older deliveries/test payloads that omit it, so
    # idempotency still holds even without the header.
    event_id = x_razorpay_event_id or hashlib.sha256(body).hexdigest()

    existing = await db.execute(
        select(BillingEvent).where(BillingEvent.provider_event_id == event_id)
    )
    if existing.scalar_one_or_none() is not None:
        # Already processed — Razorpay retries delivery, this is a safe no-op.
        return

    billing_event = BillingEvent(
        provider="razorpay",
        provider_event_id=event_id,
        event_type=event.get("event", "unknown"),
        payload=event,
    )
    db.add(billing_event)
    await db.flush()

    await _process_event(db, event)
    billing_event.processed_at = datetime.now(UTC)
    await db.commit()


async def _process_event(db: AsyncSession, event: dict[str, Any]) -> None:
    event_type = event.get("event", "")
    payment_entity = event.get("payload", {}).get("payment", {}).get("entity")

    if event_type == "payment.captured":
        if payment_entity is None:
            return
        order_id = payment_entity.get("order_id")
        payment_id = payment_entity.get("id")
        if not order_id or not payment_id:
            return

        payment_result = await db.execute(
            select(BillingPayment).where(BillingPayment.provider_order_id == order_id)
        )
        payment = payment_result.scalar_one_or_none()
        if payment is None:
            logger.warning("razorpay_payment_captured_unknown_order", order_id=order_id)
            return
        await _activate_paid_payment(db, payment, payment_id)

    elif event_type == "payment.failed":
        if payment_entity is None:
            return
        order_id = payment_entity.get("order_id")
        if not order_id:
            return
        payment_result = await db.execute(
            select(BillingPayment).where(BillingPayment.provider_order_id == order_id)
        )
        payment = payment_result.scalar_one_or_none()
        if payment is None or payment.status == "paid":
            return
        # A failed payment only affects the pending order itself — it must
        # not downgrade the org, since any prior period the org already
        # paid for is tracked independently via period_end.
        payment.status = "failed"
