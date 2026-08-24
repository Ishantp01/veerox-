from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings, settings
from apps.api.core.sessions import get_session as get_session_payload
from apps.api.db.models.account_user import AccountUser
from apps.api.db.models.org_membership import OrgMembership
from apps.api.db.session import get_session
from apps.api.redis_client import get_redis

if TYPE_CHECKING:
    from apps.api.db.models.org import Org
    from apps.api.db.models.plan import Plan


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


async def get_redis_dep() -> AsyncGenerator[aioredis.Redis, None]:
    async for client in get_redis():
        yield client


def get_settings() -> Settings:
    return settings


DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[aioredis.Redis, Depends(get_redis_dep)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

DEFAULT_ORG_ID = UUID(settings.default_org_id)
DEFAULT_OWNER_ID = UUID("00000000-0000-0000-0000-0000000000a1")


@dataclass(frozen=True)
class CurrentOrg:
    """The org + role a request's session is scoped to. A user could belong
    to multiple orgs via multiple `OrgMembership` rows, but a session picks
    one at login time (see routers/auth.py); switching orgs mid-session is
    intentionally not built yet.
    """

    org_id: UUID
    role: str


async def _resolve_session_payload(
    redis: RedisDep,
    x_session_token: str | None = Header(None),
) -> dict[str, str] | None:
    """The decoded session payload, fetched from Redis at most once per
    request.

    Several dependencies below (`get_current_user`, `get_current_org`,
    `verify_admin_or_session`, `resolve_request_org_id`,
    `resolve_analytics_scope_org_id`) all need the same session token
    resolved, and routes commonly stack more than one of them. FastAPI
    caches a `Depends()` result per callable per request, so routing them
    all through this one function collapses what would otherwise be N
    separate Redis round trips (measured ~250ms+ each against Upstash from
    local dev) into a single one.
    """
    if not x_session_token:
        return None
    return await get_session_payload(redis, x_session_token)


SessionPayloadDep = Annotated[dict[str, str] | None, Depends(_resolve_session_payload)]


async def get_current_user(
    db: DbDep,
    payload: SessionPayloadDep,
    x_admin_token: str | None = Header(None),
) -> AccountUser:
    if x_admin_token is not None and x_admin_token == settings.admin_token:
        result = await db.execute(select(AccountUser).where(AccountUser.id == DEFAULT_OWNER_ID))
        account_user = result.scalar_one_or_none()
        if account_user is not None and account_user.is_active:
            return account_user

    if payload is None:
        raise HTTPException(status_code=401, detail="Missing session token")
    result = await db.execute(
        select(AccountUser).where(AccountUser.id == UUID(payload["account_user_id"]))
    )
    account_user = result.scalar_one_or_none()
    if account_user is None or not account_user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return account_user


async def get_current_org(
    payload: SessionPayloadDep,
    x_admin_token: str | None = Header(None),
) -> CurrentOrg:
    if x_admin_token is not None and x_admin_token == settings.admin_token:
        return CurrentOrg(org_id=DEFAULT_ORG_ID, role="admin")

    if payload is None:
        raise HTTPException(status_code=401, detail="Missing session token")
    return CurrentOrg(org_id=UUID(payload["org_id"]), role=payload["role"])


CurrentUserDep = Annotated[AccountUser, Depends(get_current_user)]
CurrentOrgDep = Annotated[CurrentOrg, Depends(get_current_org)]


def require_role(*roles: str) -> Callable[[CurrentOrgDep], CurrentOrg]:
    """Dependency factory: raises 403 unless the session's role is one of
    `roles`. Usage: `org: Annotated[CurrentOrg, Depends(require_role("admin"))]`.
    """

    def _check(org: CurrentOrgDep) -> CurrentOrg:
        if org.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return org

    return _check


async def _org_is_platform_admin_owned(db: AsyncSession, org_id: UUID) -> bool:
    """True if any member of this org is a platform superuser. The platform
    operator's own org runs the product for everyone else — it doesn't buy a
    plan or get capped by one, unlike every self-signed-up customer org.
    """
    from apps.api.db.models.org_membership import OrgMembership

    result = await db.execute(
        select(AccountUser.id)
        .join(OrgMembership, OrgMembership.account_user_id == AccountUser.id)
        .where(OrgMembership.org_id == org_id, AccountUser.is_superuser.is_(True))
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


_BLOCKED_BILLING_STATUSES = ("past_due", "canceled", "incomplete")


def effective_limits(org: Org, plan: Plan | None) -> dict[str, Any]:
    """The org's real, currently-in-effect resource limits — `Org.resource_limits`
    once a recharge has populated it (it takes full precedence, key by key
    against the plan, since it's already seeded as a complete snapshot on
    every full-plan purchase — see routers/billing.py `_activate_paid_payment`),
    falling back to the catalog `Plan.limits` wholesale for an org that has
    never been touched by the new recharge logic (resource_limits is None)."""
    if org.resource_limits is not None:
        return org.resource_limits
    return dict(plan.limits) if plan is not None else {}


async def is_over_plan_limit(
    db: AsyncSession, org_id: UUID, metric: str, current_count: float
) -> bool:
    """True if the org should be blocked from consuming `metric` right now —
    either its billing has lapsed, or `current_count` has reached the org's
    effective limit for `metric` (a key in `Plan.limits`/`Org.resource_limits`,
    e.g. "max_seats"/"max_call_minutes" — see `effective_limits`).

    Credits are recharge-based: `current_count` comes from core/usage.py,
    which measures usage since the org's last recharge and never resets on
    a calendar boundary. So reaching the limit blocks the metric until the
    org buys the plan (or the relevant resource recharge) again via
    POST /billing/checkout-session — that's the normal way an org ends up
    blocked here.

    A `billing_status` outside ("trialing", "active") blocks *every* metric
    rather than just the one being checked. Nothing downgrades an org on a
    timer any more (there is no expiry worker — access ends when credits
    run out, not when a date passes); this state is now only reached by a
    failed payment or a deliberate admin action.

    Orgs with no plan assigned yet AND no recharge on record (pre-backfill
    edge case, or a customer who hasn't finished onboarding) are treated as
    unlimited rather than blocked — enforcement here is a defensive
    backstop, the primary gate is the frontend's onboarding redirect to
    /choose-plan. Note this check must run *before* the billing_status check
    below, to keep byte-for-byte the same "unlimited, status not even
    consulted" behavior a plan-less org has always had.

    Non-raising so background workers (campaign_dialer.py,
    whatsapp_dispatcher.py) can skip claiming a target without an
    HTTPException to catch; `enforce_plan_limit` below is the HTTP-route
    wrapper around this same check.
    """
    from apps.api.db.models.org import Org
    from apps.api.db.models.plan import Plan

    if await _org_is_platform_admin_owned(db, org_id):
        return False

    result = await db.execute(
        select(Plan, Org).outerjoin(Plan, Org.plan_id == Plan.id).where(Org.id == org_id)
    )
    row = result.first()
    if row is None:
        return False
    plan, target_org = row
    if plan is None and target_org.resource_limits is None:
        return False
    if target_org.billing_status in _BLOCKED_BILLING_STATUSES:
        return True
    limit = effective_limits(target_org, plan).get(metric)
    return limit is not None and current_count >= limit


async def enforce_plan_limit(
    db: AsyncSession,
    org_id: UUID,
    metric: str,
    current_count: float,
    *,
    message: str = "Credit limit reached. Please upgrade your plan to continue.",
) -> None:
    """Raise 402 if the org has reached its plan limit for `metric` — see
    `is_over_plan_limit` for the underlying check. `message` lets a caller
    give a metric-appropriate reason ("credit" reads oddly for e.g. a user
    seat cap)."""
    if await is_over_plan_limit(db, org_id, metric, current_count):
        raise HTTPException(status_code=402, detail=message)


async def is_plan_feature_enabled(db: AsyncSession, org_id: UUID, feature: str) -> bool:
    """True if the org's plan includes boolean feature flag `feature` (a key
    in `Plan.limits`, e.g. "automated_followups"). Orgs with no plan yet or
    owned by the platform admin are treated as having every feature — same
    defensive-backstop reasoning as `is_over_plan_limit` (the frontend's
    onboarding/upgrade gates are the primary enforcement for those cases).
    """
    from apps.api.db.models.org import Org
    from apps.api.db.models.plan import Plan

    if await _org_is_platform_admin_owned(db, org_id):
        return True

    result = await db.execute(
        select(Plan).join(Org, Org.plan_id == Plan.id).where(Org.id == org_id)
    )
    plan = result.scalar_one_or_none()
    if plan is None:
        return True
    return plan.limits.get(feature) is True


async def enforce_plan_feature(db: AsyncSession, org_id: UUID, feature: str) -> None:
    """Raise 403 if the org's plan doesn't include `feature` — a UI-hidden
    page redirects an ordinary click, but the API must reject the same
    request made directly (curl, a stale tab, a bookmarked URL)."""
    if not await is_plan_feature_enabled(db, org_id, feature):
        raise HTTPException(
            status_code=403,
            detail="Your plan doesn't include this feature. Upgrade to unlock it.",
        )


async def _resolve_session_membership_org_id(
    db: DbDep,
    payload: SessionPayloadDep,
) -> UUID | None:
    """The org a valid session's membership resolves to, or None.

    Shared by `verify_admin_or_session` and `resolve_request_org_id`, which
    both need to know "does this session belong to a real org membership,
    and if so which org" — routes commonly depend on both, so without this
    they'd each fire their own identical `OrgMembership` query. FastAPI's
    per-request Depends() cache collapses that back to one query.
    """
    if payload is None:
        return None
    from apps.api.db.models.org_membership import OrgMembership

    result = await db.execute(
        select(OrgMembership).where(
            OrgMembership.account_user_id == UUID(payload["account_user_id"]),
            OrgMembership.org_id == UUID(payload["org_id"]),
        )
    )
    if result.scalar_one_or_none() is None:
        return None
    return UUID(payload["org_id"])


SessionMembershipOrgIdDep = Annotated[UUID | None, Depends(_resolve_session_membership_org_id)]


async def verify_admin_or_session(
    membership_org_id: SessionMembershipOrgIdDep,
    x_admin_token: str | None = Header(None),
) -> None:
    """Router-level auth guard: accepts either the legacy shared
    `X-Admin-Token` (unchanged behavior — always checked) or, when
    `settings.require_session_auth` is on, a valid `X-Session-Token`
    resolving to an org membership. Additive by design so the existing
    admin-token-only deployments keep working unchanged with the flag off
    (see plan §7 — phased rollout, admin.py migrated behind this flag before
    the shared token is ever retired).
    """
    if x_admin_token is not None and x_admin_token == settings.admin_token:
        return

    if settings.require_session_auth and membership_org_id is not None:
        return

    raise HTTPException(status_code=403, detail="Forbidden")


async def resolve_request_org_id(
    membership_org_id: SessionMembershipOrgIdDep,
    x_admin_token: str | None = Header(None),
) -> UUID:
    """Per-request org resolution for admin.py's calling/WhatsApp/campaign/
    stats endpoints (guarded by `verify_admin_or_session`, which has already
    rejected the request by this point if neither credential is valid).

    A dashboard session resolves to that session's own org, so a call/
    message/campaign placed from the dashboard is correctly attributed (and
    plan-limited) to the org that's actually using it. `X-Admin-Token`-only
    callers (internal tooling, scripts, no session) fall back to
    `settings.default_org_id` — which is the platform admin's own seeded
    org, already exempt from plan limits via `_org_is_platform_admin_owned`
    — preserving the pre-multi-tenancy behavior of unlimited admin-token
    access rather than attributing that traffic to some arbitrary org.
    """
    _ = x_admin_token  # validity already enforced by verify_admin_or_session
    if membership_org_id is not None:
        return membership_org_id
    return UUID(settings.default_org_id)


RequestOrgDep = Annotated[UUID, Depends(resolve_request_org_id)]


async def resolve_analytics_scope_org_id(
    db: DbDep,
    payload: SessionPayloadDep,
    x_admin_token: str | None = Header(None),
) -> UUID | None:
    """Which org's data an analytics/usage read may see.

    Returns the caller's own org id for a normal customer session, so the
    dashboard's stats and reports only ever count that org's own calls,
    messages, leads and spend. Returns None — meaning "no org filter, count
    the whole platform" — only for the platform operator: an `X-Admin-Token`
    caller, or a session whose account has `is_superuser=True`.

    Deliberately separate from `resolve_request_org_id`, which answers a
    different question ("which org does this *write* belong to?") and so
    must always name a concrete org. Here, "no org" is a meaningful answer
    and the reason a superuser sees platform-wide totals while every
    customer sees only their own.
    """
    if x_admin_token is not None and x_admin_token == settings.admin_token:
        return None

    if payload is not None:
        result = await db.execute(
            select(AccountUser).where(AccountUser.id == UUID(payload["account_user_id"]))
        )
        account_user = result.scalar_one_or_none()
        if account_user is not None and account_user.is_active and account_user.is_superuser:
            return None
        return UUID(payload["org_id"])

    # No usable credential reached here only because verify_admin_or_session
    # already let the request through on the legacy admin token path; fall
    # back to the platform admin's own seeded org rather than leaking
    # platform-wide totals to an unidentified caller.
    return UUID(settings.default_org_id)


AnalyticsScopeDep = Annotated[UUID | None, Depends(resolve_analytics_scope_org_id)]


async def verify_platform_admin(
    db: DbDep,
    payload: SessionPayloadDep,
    x_admin_token: str | None = Header(None),
) -> None:
    """Stricter than `verify_admin_or_session`: for platform-wide resources
    (the plan catalog) rather than a single org's own data. `X-Admin-Token`
    still always passes; a session only passes if that specific account has
    `is_superuser=True` — unlike `verify_admin_or_session`, ANY org member's
    valid session is not enough, since once self-signup is in use that would
    let any customer org edit pricing for every other org on the platform.
    """
    if x_admin_token is not None and x_admin_token == settings.admin_token:
        return

    if payload is not None:
        result = await db.execute(
            select(AccountUser).where(AccountUser.id == UUID(payload["account_user_id"]))
        )
        account_user = result.scalar_one_or_none()
        if account_user is not None and account_user.is_active and account_user.is_superuser:
            return

    raise HTTPException(status_code=403, detail="Forbidden")
