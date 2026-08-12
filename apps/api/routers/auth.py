"""Dashboard login: token-based accounts, no password.

A brand new org + its first (admin) account is only ever created by a
platform admin (POST /auth/provision-org) — there's no public signup. From
there, an org's admin can grow their own team via POST /team/members
(routers/team.py) without platform-admin involvement. Every account gets
one permanent random token (see core/security.py) that IS the login
credential; there's nothing to reset or forget beyond asking an admin
to re-invite or a platform admin to re-provision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apps.api.channels.voice import failover as voice_failover
from apps.api.channels.voice.number_provider import resolve_calling_number
from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.core.sessions import create_session, delete_session
from apps.api.db.models.account_user import AccountUser
from apps.api.db.models.org import Org
from apps.api.db.models.org_membership import OrgMembership
from apps.api.deps import CurrentUserDep, DbDep, RedisDep, verify_platform_admin
from apps.api.schemas.auth import LoginIn, MeOut, ProvisionOrgIn, ProvisionOrgOut, SessionOut

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

PlatformAdminDep = Annotated[None, Depends(verify_platform_admin)]

DEFAULT_ORG_ID = UUID(settings.default_org_id)
DEFAULT_OWNER_ID = UUID("00000000-0000-0000-0000-0000000000a1")
DEFAULT_OWNER_EMAIL = "owner@veerox-admin.com"


async def _ensure_default_org_owner(db: DbDep) -> tuple[AccountUser, OrgMembership]:
    """Return the (account, membership) pair for the default org's admin
    account, creating whichever half is missing.

    One combined outer-join lookup instead of two separate selects — DB is a
    remote Neon instance (see .env), so each extra round trip is real
    latency on the admin-token login path, not just an extra local query.
    """
    result = await db.execute(
        select(AccountUser, OrgMembership)
        .outerjoin(
            OrgMembership,
            (OrgMembership.account_user_id == AccountUser.id)
            & (OrgMembership.org_id == DEFAULT_ORG_ID),
        )
        .where(AccountUser.id == DEFAULT_OWNER_ID)
    )
    row = result.first()
    account_user = row[0] if row else None
    membership = row[1] if row else None

    if account_user is None:
        account_user = AccountUser(
            id=DEFAULT_OWNER_ID,
            email=DEFAULT_OWNER_EMAIL,
            token_hash=hash_token(settings.admin_token),
            full_name="Veerox Owner",
            is_active=True,
            is_superuser=True,
        )
        db.add(account_user)
    elif account_user.token_hash != hash_token(settings.admin_token):
        account_user.token_hash = hash_token(settings.admin_token)
        account_user.is_active = True
        account_user.is_superuser = True

    if membership is None:
        membership = OrgMembership(
            org_id=DEFAULT_ORG_ID,
            account_user_id=DEFAULT_OWNER_ID,
            role="admin",
            joined_at=datetime.now(UTC),
        )
        db.add(membership)

    await db.flush()
    return account_user, membership


@router.post("/provision-org", response_model=ProvisionOrgOut, status_code=201)
async def provision_org(
    payload: ProvisionOrgIn, db: DbDep, _admin: PlatformAdminDep
) -> ProvisionOrgOut:
    """Platform-admin-only replacement for the old public /auth/signup —
    creates a brand new org plus its admin account. Not exposed to
    end users; a customer never lands here themselves.
    """
    existing = await db.execute(select(AccountUser).where(AccountUser.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Email already registered")

    # The entered calling number is checked against both the Plivo and
    # Twilio accounts and stored under whichever one owns it (see
    # channels/voice/number_provider.py::detect_provider) so
    # channels/voice/failover.py dials from — and can fail over around —
    # the correct provider for this org.
    plivo_number: str | None = None
    twilio_number: str | None = None
    if payload.plivo_phone_number:
        digits, provider = await resolve_calling_number(payload.plivo_phone_number)
        if provider == "twilio":
            twilio_number = digits
        else:
            plivo_number = digits

    # No plan assigned yet on purpose: `Org.plan_id is None` is exactly the
    # signal the frontend's dashboard layout gates on to force new orgs
    # through /billing (choose-a-plan, even the free one) before anything
    # else in the app becomes reachable. See DashboardLayout in apps/web.
    org = Org(
        name=payload.org_name,
        plivo_phone_number=plivo_number,
        twilio_phone_number=twilio_number,
        whatsapp_phone_number_id=payload.whatsapp_phone_number_id.strip()
        if payload.whatsapp_phone_number_id
        else None,
    )
    db.add(org)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="One of these numbers is already assigned to another org."
        )

    login_token = generate_login_token()
    account_user = AccountUser(
        email=payload.email,
        token_hash=hash_token(login_token),
        full_name=payload.full_name,
        mobile=payload.mobile,
    )
    db.add(account_user)
    await db.flush()

    db.add(
        OrgMembership(
            org_id=org.id,
            account_user_id=account_user.id,
            role="admin",
            joined_at=datetime.now(UTC),
        )
    )
    await db.commit()

    sms_sent = False
    try:
        await voice_failover.send_sms(
            payload.mobile,
            f"Welcome to Veerox. Your login token: {login_token}",
        )
        sms_sent = True
    except httpx.HTTPError as exc:
        # Best-effort: the token is also shown once in the dashboard
        # response, so a failed SMS doesn't block org creation.
        logger.warning("provision_org_sms_failed", mobile=payload.mobile, error=str(exc))

    return ProvisionOrgOut(
        org_id=org.id,
        account_user_id=account_user.id,
        email=account_user.email,
        login_token=login_token,
        sms_sent=sms_sent,
    )


@router.post("/login", response_model=SessionOut)
async def login(payload: LoginIn, db: DbDep, redis: RedisDep) -> SessionOut:
    account_user: AccountUser | None
    membership: OrgMembership | None

    # The shared admin token never matches a real hashed token (see
    # core/security.py — every real token is high-entropy and unique), so
    # looking it up by hash first is a guaranteed-miss round trip on this
    # path. Going straight to the dev/admin account skips it.
    if payload.token == settings.admin_token:
        account_user, membership = await _ensure_default_org_owner(db)
    else:
        # One outer-join query instead of a separate account lookup + a
        # separate membership lookup — real latency savings against a
        # remote DB (see .env), not just fewer local queries.
        result = await db.execute(
            select(AccountUser, OrgMembership)
            .outerjoin(OrgMembership, OrgMembership.account_user_id == AccountUser.id)
            .where(AccountUser.token_hash == hash_token(payload.token))
            .order_by(OrgMembership.created_at)
            .limit(1)
        )
        row = result.first()
        account_user, membership = row if row else (None, None)

    if account_user is None or not account_user.is_active:
        raise HTTPException(status_code=401, detail="Invalid login token")
    if membership is None:
        raise HTTPException(status_code=403, detail="Account has no org membership")

    account_user.last_login_at = datetime.now(UTC)
    await db.commit()

    token = await create_session(
        redis, account_user_id=account_user.id, org_id=membership.org_id, role=membership.role
    )
    org = await db.get(Org, membership.org_id)
    return SessionOut(
        token=token,
        org_id=membership.org_id,
        org_name=org.name if org else "",
        role=membership.role,
        account_user_id=account_user.id,
        email=account_user.email,
        full_name=account_user.full_name,
        is_superuser=account_user.is_superuser,
    )


@router.post("/logout", status_code=204)
async def logout(redis: RedisDep, x_session_token: str | None = Header(None)) -> None:
    if x_session_token:
        await delete_session(redis, x_session_token)


@router.get("/me", response_model=MeOut)
async def me(current_user: CurrentUserDep, db: DbDep) -> MeOut:
    result = await db.execute(
        select(OrgMembership)
        .where(OrgMembership.account_user_id == current_user.id)
        .order_by(OrgMembership.created_at)
    )
    membership = result.scalars().first()
    if membership is None:
        raise HTTPException(status_code=403, detail="Account has no org membership")
    org = await db.get(Org, membership.org_id)
    return MeOut(
        org_id=membership.org_id,
        org_name=org.name if org else "",
        role=membership.role,
        account_user_id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        is_superuser=current_user.is_superuser,
    )
