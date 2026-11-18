"""Usage limits from an org's active Razorpay plan (payment_plans.max_*).

Rules:
- The owner (platform) org is always exempt.
- An org with no active paid plan is NOT limited here; the manual license
  system remains its only gate. Limits apply only while a paid plan is active.
- A NULL limit on the plan means unlimited for that resource.
- Usage is counted inside the plan's paid period (team members: current total).
Independent of Org.license_*; nothing here reads or writes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.call_campaign import CallCampaign
from apps.api.db.models.org_membership import OrgMembership
from apps.api.db.models.razorpay_payment import PaymentPlan, RazorpayPayment
from apps.api.db.models.usage_event import UsageEvent

RESOURCES = (
    "call_minutes",
    "whatsapp_messages",
    "team_members",
    "voice_campaigns",
    "whatsapp_campaigns",
)

_PLAN_COLUMN = {r: f"max_{r}" for r in RESOURCES}

# What the user can do about it, per resource (shown after the limit sentence).
RESOURCE_HINT = {
    "call_minutes": "Upgrade or renew your plan to keep calling.",
    "whatsapp_messages": "Upgrade or renew your plan to keep sending messages.",
    "team_members": "Upgrade your plan to add more team members.",
    "voice_campaigns": "Upgrade your plan to create more voice campaigns.",
    "whatsapp_campaigns": "Upgrade your plan to create more WhatsApp campaigns.",
}

RESOURCE_LABEL = {
    "call_minutes": "call minutes",
    "whatsapp_messages": "WhatsApp messages",
    "team_members": "team members",
    "voice_campaigns": "voice campaigns",
    "whatsapp_campaigns": "WhatsApp campaigns",
}


@dataclass
class ActivePlan:
    plan: PaymentPlan
    period_start: datetime
    period_end: datetime
    # Extra allowance from paid add-on packs, per resource (resource -> amount).
    extras: dict[str, int] | None = None


@dataclass
class ResourceUsage:
    resource: str
    limit: int | None
    used: float

    @property
    def exceeded(self) -> bool:
        return self.limit is not None and self.used >= self.limit


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def get_active_plan(db: AsyncSession, org_id: UUID) -> ActivePlan | None:
    # Imported here: deps pulls in many models; avoid a cycle at module load.
    from apps.api.deps import _org_is_platform_admin_owned

    if await _org_is_platform_admin_owned(db, org_id):
        return None
    now = datetime.now(UTC)
    result = await db.execute(
        select(RazorpayPayment)
        .join(PaymentPlan, PaymentPlan.id == RazorpayPayment.plan_id)
        .where(
            RazorpayPayment.org_id == org_id,
            RazorpayPayment.status == "paid",
            RazorpayPayment.period_start <= now,
            RazorpayPayment.period_end > now,
            PaymentPlan.is_addon.is_(False),
        )
        .order_by(RazorpayPayment.period_end.desc())
        .limit(1)
    )
    payment = result.scalar_one_or_none()
    if payment is None or payment.period_start is None or payment.period_end is None:
        return None
    plan = await db.get(PaymentPlan, payment.plan_id)
    if plan is None:
        return None
    # Add-on packs bought during this base period (they end with it).
    extras: dict[str, int] = {}
    addon_rows = await db.execute(
        select(PaymentPlan)
        .join(RazorpayPayment, PaymentPlan.id == RazorpayPayment.plan_id)
        .where(
            RazorpayPayment.org_id == org_id,
            RazorpayPayment.status == "paid",
            RazorpayPayment.period_end == payment.period_end,
            PaymentPlan.is_addon.is_(True),
        )
    )
    for addon in addon_rows.scalars():
        for resource, column in _PLAN_COLUMN.items():
            if resource == "team_members":
                continue  # handled below: not tied to a billing period
            extras[resource] = extras.get(resource, 0) + (getattr(addon, column) or 0)
    # Team size is a standing total, not a per-period quota: a team add-on keeps
    # applying across renewals for as long as the org has an active plan.
    team_addons = await db.execute(
        select(PaymentPlan.max_team_members)
        .join(RazorpayPayment, PaymentPlan.id == RazorpayPayment.plan_id)
        .where(
            RazorpayPayment.org_id == org_id,
            RazorpayPayment.status == "paid",
            PaymentPlan.is_addon.is_(True),
        )
    )
    extras["team_members"] = sum(v or 0 for v in team_addons.scalars())
    # Usage window = the current payment's own paid period.
    return ActivePlan(
        plan, _aware(payment.period_start), _aware(payment.period_end), extras
    )


async def _used(
    db: AsyncSession, org_id: UUID, resource: str, since: datetime, until: datetime
) -> float:
    if resource == "team_members":
        count = await db.scalar(
            select(func.count())
            .select_from(OrgMembership)
            .where(OrgMembership.org_id == org_id, OrgMembership.invited_by_id.is_not(None))
        )
        return float(count or 0)
    if resource in ("voice_campaigns", "whatsapp_campaigns"):
        channel = "voice" if resource == "voice_campaigns" else "whatsapp"
        count = await db.scalar(
            select(func.count())
            .select_from(CallCampaign)
            .where(
                CallCampaign.org_id == org_id,
                # "mixed" campaigns (voice + WhatsApp in one upload) use both.
                CallCampaign.channel.in_((channel, "mixed")),
                CallCampaign.created_at >= since,
                CallCampaign.created_at < until,
            )
        )
        return float(count or 0)
    usage_type = "voice_seconds" if resource == "call_minutes" else "whatsapp_messages"
    # Campaign traffic (calls / sends made by a campaign) is limited by the
    # campaign counts, not by these meters.
    not_campaign = [
        or_(
            UsageEvent.metadata_json["campaign"].as_boolean().is_(None),
            UsageEvent.metadata_json["campaign"].as_boolean().is_(False),
        ),
        UsageEvent.source != "whatsapp_dispatcher",
    ]
    total = await db.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.org_id == org_id,
            UsageEvent.usage_type == usage_type,
            UsageEvent.created_at >= since,
            UsageEvent.created_at < until,
            *not_campaign,
        )
    )
    value = Decimal(total or 0)
    return float(value / 60 if resource == "call_minutes" else value)


async def get_resource_usage(
    db: AsyncSession, org_id: UUID, resource: str, active: ActivePlan | None = None
) -> ResourceUsage | None:
    """None = no limit applies (owner org, or no active plan)."""
    active = active or await get_active_plan(db, org_id)
    if active is None:
        return None
    limit = getattr(active.plan, _PLAN_COLUMN[resource])
    if limit is not None:  # NULL base stays unlimited
        limit += (active.extras or {}).get(resource, 0)
    used = await _used(db, org_id, resource, active.period_start, active.period_end)
    return ResourceUsage(resource, limit, used)


async def is_within_plan_limit(db: AsyncSession, org_id: UUID, resource: str) -> bool:
    """Non-raising check for background workers."""
    usage = await get_resource_usage(db, org_id, resource)
    return usage is None or not usage.exceeded


async def enforce_plan_limit(db: AsyncSession, org_id: UUID, resource: str) -> None:
    """Raise 403 when the plan's limit for `resource` is already used up."""
    usage = await get_resource_usage(db, org_id, resource)
    if usage is not None and usage.exceeded:
        label = RESOURCE_LABEL[resource]
        raise HTTPException(
            status_code=403,
            detail={
                "error": "plan_limit_reached",
                "resource": resource,
                "limit": usage.limit,
                "used": usage.used,
                "message": (
                    f"You've reached your plan's limit of {usage.limit} {label}. "
                    f"{RESOURCE_HINT[resource]}"
                ),
            },
        )


async def all_usage(db: AsyncSession, org_id: UUID) -> list[ResourceUsage]:
    active = await get_active_plan(db, org_id)
    if active is None:
        return []
    out: list[ResourceUsage] = []
    for resource in RESOURCES:
        usage = await get_resource_usage(db, org_id, resource, active)
        if usage is not None:
            out.append(usage)
    return out
