from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
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
    db: DbDep,
    payload: SessionPayloadDep,
    x_admin_token: str | None = Header(None),
) -> CurrentOrg:
    if x_admin_token is not None and x_admin_token == settings.admin_token:
        return CurrentOrg(org_id=DEFAULT_ORG_ID, role="admin")

    if payload is None:
        raise HTTPException(status_code=401, detail="Missing session token")
    org_id = UUID(payload["org_id"])
    await enforce_org_license(db, org_id)
    return CurrentOrg(org_id=org_id, role=payload["role"])


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


async def is_org_license_active(db: AsyncSession, org_id: UUID) -> bool:
    """False if this org's license is suspended or expired — the platform's
    own operating org is always exempt (see `_org_is_platform_admin_owned`),
    same as it always was for plan-limit enforcement. Non-raising so
    background workers can check without an HTTPException to catch;
    `enforce_org_license` below is the HTTP-route wrapper around this."""
    from apps.api.db.models.org import Org

    if await _org_is_platform_admin_owned(db, org_id):
        return True

    result = await db.execute(select(Org.license_status).where(Org.id == org_id))
    license_status = result.scalar_one_or_none()
    if license_status is None:
        return True
    return license_status == "active"


async def enforce_org_license(db: AsyncSession, org_id: UUID) -> None:
    """Raise 403 if `org_id`'s license is suspended or expired. Wired into
    `resolve_request_org_id` below so it runs on every org-scoped request
    without per-route changes."""
    if not await is_org_license_active(db, org_id):
        from apps.api.db.models.org import Org

        result = await db.execute(select(Org.license_status).where(Org.id == org_id))
        license_status = result.scalar_one()
        raise HTTPException(
            status_code=403,
            detail={
                "error": "license_inactive",
                "status": license_status,
                "message": "Your organization's license is inactive. Contact the platform admin.",
            },
        )


async def is_org_feature_enabled(db: AsyncSession, org_id: UUID, feature: str) -> bool:
    """False if this org has been explicitly restricted from `feature` — the
    platform's own operating org is always exempt (same as
    `is_org_license_active`). `Org.enabled_features` is NULL for every org by
    default, meaning "unrestricted"; only an explicit list (which may be
    empty) narrows access. Non-raising so it can be reused outside HTTP
    routes; `require_feature` below is the route-dependency wrapper."""
    from apps.api.db.models.org import Org

    if await _org_is_platform_admin_owned(db, org_id):
        return True

    result = await db.execute(select(Org.enabled_features).where(Org.id == org_id))
    enabled_features = result.scalar_one_or_none()
    if enabled_features is None:
        return True
    return feature in enabled_features


def require_feature(feature: str) -> Callable[[UUID, AsyncSession], Awaitable[None]]:
    """Dependency factory gating a whole router behind one of
    db/models/org.py's AVAILABLE_ORG_FEATURES — e.g.
    `APIRouter(..., dependencies=[Depends(require_feature("crm"))])`. Raises
    403 if the requesting org has been restricted from this feature by a
    platform admin (see schemas/billing.py's OrgUpdateIn.enabled_features)."""

    async def _check(org_id: RequestOrgDep, db: DbDep) -> None:
        if not await is_org_feature_enabled(db, org_id, feature):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "feature_disabled",
                    "feature": feature,
                    "message": "This feature is not enabled for your organization. Contact the platform admin.",
                },
            )

    return _check


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


async def verify_platform_team_member(
    membership_org_id: SessionMembershipOrgIdDep,
    x_admin_token: str | None = Header(None),
) -> None:
    """Gate for platform-team-only resources that every Veerox staff account
    should reach, not just the single seeded superuser — e.g. the cross-org
    support ticket queue (routers/tickets.py's admin_router). Passes for the
    shared `X-Admin-Token`, or any session whose org membership is the
    platform operator's own org (`DEFAULT_ORG_ID` — the org every Veerox
    staff account is invited onto via POST /team/members).

    Deliberately org-based rather than `is_superuser`-based: `is_superuser`
    is a narrower, more sensitive flag (it also unlocks platform-wide
    billing/plan-catalog control via `verify_platform_admin`) that a support
    rep invited onto the platform org doesn't need just to triage tickets.
    Unlike `verify_admin_or_session`, this doesn't depend on
    `settings.require_session_auth` — membership in the platform org is
    itself the authorization, on or off.
    """
    if x_admin_token is not None and x_admin_token == settings.admin_token:
        return
    if membership_org_id is not None and membership_org_id == DEFAULT_ORG_ID:
        return
    raise HTTPException(status_code=403, detail="Forbidden")


async def resolve_request_org_id(
    db: DbDep,
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
    org_id = membership_org_id if membership_org_id is not None else UUID(settings.default_org_id)
    await enforce_org_license(db, org_id)
    return org_id


RequestOrgDep = Annotated[UUID, Depends(resolve_request_org_id)]


async def resolve_request_account_user_id(
    payload: SessionPayloadDep,
    x_admin_token: str | None = Header(None),
) -> UUID:
    """Which account_user this write should be attributed to — e.g.
    routers/crm.py's Contact.created_by_account_user_id, so a contact a
    teammate adds is recorded as theirs.

    A dashboard session resolves to that session's own account_user_id.
    `X-Admin-Token`-only callers (internal tooling, scripts, no session)
    fall back to `DEFAULT_OWNER_ID` — the platform admin's own seeded
    account — same fallback convention as `resolve_request_org_id`'s
    `settings.default_org_id`, rather than attributing that traffic to some
    arbitrary account.
    """
    _ = x_admin_token  # validity already enforced by verify_admin_or_session
    if payload is not None:
        return UUID(payload["account_user_id"])
    return DEFAULT_OWNER_ID


RequestAccountUserDep = Annotated[UUID, Depends(resolve_request_account_user_id)]


async def resolve_member_scope_account_user_id(
    db: DbDep,
    payload: SessionPayloadDep,
    x_admin_token: str | None = Header(None),
) -> UUID | None:
    """Which account_user a caller's view of per-member data is restricted to.

    Returns None — "see everything in the org" — for the shared
    `X-Admin-Token`, a platform superuser, or a `role=="admin"` org
    membership. Returns the caller's own account_user id for a
    `role=="member"` session, who should only see appointments/conversations/
    follow-ups tied to a `Lead` they've claimed
    (`Lead.claimed_by_account_user_id`).

    The routers-level equivalent of `admin.py::_member_lead_scope`, usable
    from routers that only depend on `verify_admin_or_session` +
    `RequestOrgDep` (appointments.py, conversations.py, follow_ups.py) rather
    than the analytics-scope dependency stack `admin.py` uses.
    """
    if x_admin_token is not None and x_admin_token == settings.admin_token:
        return None
    if payload is None:
        return None

    account_user_id = UUID(payload["account_user_id"])
    result = await db.execute(select(AccountUser).where(AccountUser.id == account_user_id))
    account_user = result.scalar_one_or_none()
    if account_user is not None and account_user.is_active and account_user.is_superuser:
        return None

    membership = await db.execute(
        select(OrgMembership).where(
            OrgMembership.account_user_id == account_user_id,
            OrgMembership.org_id == UUID(payload["org_id"]),
        )
    )
    row = membership.scalar_one_or_none()
    if row is None or row.role == "admin":
        return None
    return account_user_id


MemberScopeDep = Annotated[UUID | None, Depends(resolve_member_scope_account_user_id)]


def owned_lead_ids(scope_account_user_id: UUID):
    """Subquery of `leads.id` claimed by `scope_account_user_id` — the set of
    leads a `role=="member"` caller owns. Pair with `Appointment.lead_id.in_(...)`
    / `FollowUpTask.lead_id.in_(...)`."""
    from apps.api.db.models.lead import Lead

    return select(Lead.id).where(Lead.claimed_by_account_user_id == scope_account_user_id)


def owned_lead_user_ids(scope_account_user_id: UUID):
    """Subquery of customer `users.id` behind the leads a member owns. Pair
    with `Conversation.user_id.in_(...)`."""
    from apps.api.db.models.lead import Lead

    return select(Lead.user_id).where(Lead.claimed_by_account_user_id == scope_account_user_id)


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
