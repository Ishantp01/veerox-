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
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from apps.api.channels.email import brevo_client
from apps.api.channels.voice import failover as voice_failover
from apps.api.channels.voice.org_numbers import get_default_numbers, replace_org_phone_numbers
from apps.api.channels.voice.plivo_provisioning import fire_and_forget_register_org_plivo_numbers
from apps.api.channels.whatsapp.webhook_registration import fire_and_forget_register_org_webhook
from apps.api.config import settings
from apps.api.core.crypto import encrypt_secret
from apps.api.core.org_credentials import (
    resolve_plivo_credentials,
    resolve_twilio_credentials,
)
from apps.api.core.security import generate_login_token, hash_token
from apps.api.core.sessions import create_session, delete_session, invalidate_user_sessions
from apps.api.db.models.account_user import AccountUser
from apps.api.db.models.org import Org
from apps.api.db.models.org_membership import OrgMembership
from apps.api.db.session import AsyncSessionLocal
from apps.api.deps import CurrentUserDep, DbDep, RedisDep, verify_platform_admin
from apps.api.rate_limit import limiter
from apps.api.schemas.auth import (
    ForgotTokenIn,
    ForgotTokenOut,
    LoginIn,
    LoginOrgChoiceOut,
    MeOut,
    OrgChoice,
    ProvisionOrgIn,
    ProvisionOrgOut,
    SessionOut,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

PlatformAdminDep = Annotated[None, Depends(verify_platform_admin)]

DEFAULT_ORG_ID = UUID(settings.default_org_id)
DEFAULT_OWNER_ID = UUID("00000000-0000-0000-0000-0000000000a1")
DEFAULT_OWNER_EMAIL = "owner@veerox-admin.com"


async def _ensure_default_org_owner(db: DbDep) -> tuple[AccountUser, OrgMembership, str | None]:
    """Return the (account, membership, org_name) triple for the default
    org's admin account, creating whichever half is missing.

    One combined outer-join lookup (now also pulling Org.name) instead of
    separate selects — DB is a remote Neon instance (see .env), so each
    extra round trip is real latency on the admin-token login path, not
    just an extra local query. The Org.name join costs nothing extra here
    since DEFAULT_ORG_ID is a constant, and it saves login() a whole
    separate round trip to fetch the org afterward.
    """
    result = await db.execute(
        select(AccountUser, OrgMembership, Org.name)
        .outerjoin(
            OrgMembership,
            (OrgMembership.account_user_id == AccountUser.id)
            & (OrgMembership.org_id == DEFAULT_ORG_ID),
        )
        .outerjoin(Org, Org.id == DEFAULT_ORG_ID)
        .where(AccountUser.id == DEFAULT_OWNER_ID)
    )
    row = result.first()
    account_user = row[0] if row else None
    membership = row[1] if row else None
    org_name = row[2] if row else None

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

    # Commit (not just flush) — this function may be creating the default
    # org owner for the first time, and the login endpoint below no longer
    # commits on its own (last_login_at moved to a background task), so
    # this is the only place those rows get persisted.
    await db.commit()
    return account_user, membership, org_name


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

    # No license issued yet on purpose — the org starts "active" with no
    # expiry (never auto-expires) until the platform admin issues its first
    # real license via POST /billing/orgs/{id}/license/issue.
    org = Org(
        name=payload.org_name,
        enabled_features=payload.enabled_features,
        max_team_members=payload.max_team_members,
    )
    if payload.plivo_auth_id and payload.plivo_auth_token:
        org.plivo_auth_id = payload.plivo_auth_id.strip()
        org.plivo_auth_token_encrypted = encrypt_secret(payload.plivo_auth_token.strip())
    if payload.twilio_account_sid and payload.twilio_auth_token:
        org.twilio_account_sid = payload.twilio_account_sid.strip()
        org.twilio_auth_token_encrypted = encrypt_secret(payload.twilio_auth_token.strip())
    if payload.meta_app_id and payload.meta_app_secret and payload.meta_access_token:
        org.meta_app_id = payload.meta_app_id.strip()
        org.meta_app_secret_encrypted = encrypt_secret(payload.meta_app_secret.strip())
        org.meta_access_token_encrypted = encrypt_secret(payload.meta_access_token.strip())
        org.meta_whatsapp_business_account_id = (
            payload.meta_whatsapp_business_account_id.strip()
            if payload.meta_whatsapp_business_account_id
            else None
        )
        org.meta_verify_token_encrypted = (
            encrypt_secret(payload.meta_verify_token.strip()) if payload.meta_verify_token else None
        )
    if payload.openai_api_key:
        org.openai_api_key_encrypted = encrypt_secret(payload.openai_api_key.strip())
    db.add(org)
    try:
        await db.flush()
        if payload.phone_numbers:
            await replace_org_phone_numbers(db, org.id, payload.phone_numbers)
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

    fire_and_forget_register_org_plivo_numbers(org.id)
    fire_and_forget_register_org_webhook(org.id)

    sms_sent = False
    plivo_creds = resolve_plivo_credentials(org)
    twilio_creds = resolve_twilio_credentials(org)
    plivo_from, twilio_from = await get_default_numbers(db, org.id)
    try:
        await voice_failover.send_sms(
            plivo_creds,
            twilio_creds,
            payload.mobile,
            f"Welcome to Veerox. Your login token: {login_token}",
            plivo_from_number=plivo_from,
            twilio_from_number=twilio_from,
        )
        sms_sent = True
    except (httpx.HTTPError, RuntimeError) as exc:
        # Best-effort: the token is also shown once in the dashboard
        # response, so a failed SMS doesn't block org creation. RuntimeError
        # is expected/common here — a brand new org often has no calling
        # credentials configured yet.
        logger.warning("provision_org_sms_failed", mobile=payload.mobile, error=str(exc))

    return ProvisionOrgOut(
        org_id=org.id,
        account_user_id=account_user.id,
        email=account_user.email,
        login_token=login_token,
        sms_sent=sms_sent,
    )


async def _record_last_login(account_user_id: UUID) -> None:
    """Runs as a background task, off the login request's critical path, in
    its own session — the request's `db` session may already be torn down
    by the time this executes. last_login_at is purely informational, so
    it doesn't need to block the client getting its session token back
    (saves a full extra round trip against the remote Neon instance)."""
    async with AsyncSessionLocal() as session:
        account_user = await session.get(AccountUser, account_user_id)
        if account_user is not None:
            account_user.last_login_at = datetime.now(UTC)
            await session.commit()


@router.post("/login", response_model=SessionOut | LoginOrgChoiceOut)
async def login(
    payload: LoginIn, db: DbDep, redis: RedisDep, background_tasks: BackgroundTasks
) -> SessionOut | LoginOrgChoiceOut:
    account_user: AccountUser | None
    membership: OrgMembership | None
    org_name: str | None

    # The shared admin token never matches a real hashed token (see
    # core/security.py — every real token is high-entropy and unique), so
    # looking it up by hash first is a guaranteed-miss round trip on this
    # path. Going straight to the dev/admin account skips it.
    license_status = "active"
    license_expires_at: datetime | None = None
    enabled_features: list[str] | None = None
    if payload.token == settings.admin_token:
        account_user, membership, org_name = await _ensure_default_org_owner(db)
    else:
        # One outer-join query instead of a separate account lookup + a
        # separate membership lookup + a separate org lookup — real latency
        # savings against a remote DB (see .env), not just fewer local
        # queries. Org.name/license fields ride along in this same query
        # instead of a trailing db.get(Org, ...) after the session is created.
        # No `.limit(1)` here (unlike before) — a token can match more than
        # one OrgMembership (routers/team.py's invite_member reuses an
        # existing account across orgs), and picking one via `.order_by(...
        # created_at)` silently landed the caller in whichever org they
        # joined *first*, regardless of which one they meant to use.
        result = await db.execute(
            select(
                AccountUser,
                OrgMembership,
                Org.name,
                Org.license_status,
                Org.license_expires_at,
                Org.enabled_features,
            )
            .outerjoin(OrgMembership, OrgMembership.account_user_id == AccountUser.id)
            .outerjoin(Org, Org.id == OrgMembership.org_id)
            .where(AccountUser.token_hash == hash_token(payload.token))
            .order_by(OrgMembership.created_at)
        )
        rows = result.all()
        account_user = rows[0][0] if rows else None
        membership_rows = [r for r in rows if r[1] is not None]

        if payload.org_id is not None:
            membership_rows = [r for r in membership_rows if r[1].org_id == payload.org_id]
            if not membership_rows:
                raise HTTPException(status_code=403, detail="Invalid organization for this account")

        if len(membership_rows) > 1:
            if account_user is None or not account_user.is_active:
                raise HTTPException(status_code=401, detail="Invalid login token")
            return LoginOrgChoiceOut(
                orgs=[
                    OrgChoice(org_id=r[1].org_id, org_name=r[2] or "", role=r[1].role)
                    for r in membership_rows
                ]
            )

        if membership_rows:
            _, membership, org_name, license_status, license_expires_at, enabled_features = (
                membership_rows[0]
            )
        else:
            membership, org_name = None, None

    if account_user is None or not account_user.is_active:
        raise HTTPException(status_code=401, detail="Invalid login token")
    if membership is None:
        raise HTTPException(status_code=403, detail="Account has no org membership")

    is_platform_org = membership.org_id == DEFAULT_ORG_ID
    if is_platform_org:
        license_status, license_expires_at, enabled_features = "active", None, None

    background_tasks.add_task(_record_last_login, account_user.id)

    token = await create_session(
        redis, account_user_id=account_user.id, org_id=membership.org_id, role=membership.role
    )
    return SessionOut(
        token=token,
        org_id=membership.org_id,
        org_name=org_name or "",
        role=membership.role,
        account_user_id=account_user.id,
        email=account_user.email,
        full_name=account_user.full_name,
        is_superuser=account_user.is_superuser,
        is_platform_org=is_platform_org,
        license_status=license_status,
        license_expires_at=license_expires_at.isoformat() if license_expires_at else None,
        enabled_features=enabled_features,
    )


_FORGOT_TOKEN_GENERIC_MESSAGE = "If an account matches, a new login token has been sent."


@router.post("/forgot-token", response_model=ForgotTokenOut)
@limiter.limit("5/minute")
async def forgot_token(
    request: Request, payload: ForgotTokenIn, db: DbDep, redis: RedisDep
) -> ForgotTokenOut:
    """Self-service token recovery: identify by email or mobile, rotate the
    token, kill existing sessions, and deliver the new token by whichever
    channel matched. Public and unauthenticated, so the response is always
    the same generic message regardless of whether a match was found or
    delivery succeeded — anything else would let a caller enumerate which
    emails/numbers have accounts (see routers/team.py's
    regenerate_member_token for the authenticated equivalent of this
    rotate-and-invalidate mutation).
    """
    identifier = payload.identifier.strip()
    is_email = "@" in identifier

    if is_email:
        result = await db.execute(
            select(AccountUser).where(func.lower(AccountUser.email) == identifier.lower())
        )
    else:
        result = await db.execute(select(AccountUser).where(AccountUser.mobile == identifier))
    account_user = result.scalars().first()

    if account_user is not None and account_user.is_active:
        login_token = generate_login_token()
        account_user.token_hash = hash_token(login_token)
        await db.commit()
        await invalidate_user_sessions(redis, account_user.id)

        try:
            if is_email:
                await brevo_client.send_email(
                    account_user.email,
                    "Your new Veerox login token",
                    f"<p>Your new login token: <b>{login_token}</b></p>"
                    "<p>Your previous token no longer works.</p>",
                )
            elif account_user.mobile:
                # This account_user has no org_id column of its own — resolve
                # it via its (first-joined) OrgMembership, same ordering
                # login() above uses, so the SMS goes out from THIS account's
                # own org's Plivo/Twilio credentials/numbers.
                org_result = await db.execute(
                    select(Org)
                    .join(OrgMembership, OrgMembership.org_id == Org.id)
                    .where(OrgMembership.account_user_id == account_user.id)
                    .order_by(OrgMembership.created_at)
                    .limit(1)
                )
                org_record = org_result.scalars().first()
                plivo_creds = resolve_plivo_credentials(org_record)
                twilio_creds = resolve_twilio_credentials(org_record)
                plivo_from, twilio_from = (
                    await get_default_numbers(db, org_record.id) if org_record else (None, None)
                )
                await voice_failover.send_sms(
                    plivo_creds,
                    twilio_creds,
                    account_user.mobile,
                    f"Your new Veerox login token: {login_token}",
                    plivo_from_number=plivo_from,
                    twilio_from_number=twilio_from,
                )
        except (httpx.HTTPError, RuntimeError) as exc:
            # Best-effort, same as provision_org's SMS send: the mutation
            # (rotate + invalidate) already happened, so a delivery failure
            # can't be surfaced without also leaking that a match was found.
            logger.warning("forgot_token_delivery_failed", identifier=identifier, error=str(exc))

    return ForgotTokenOut(message=_FORGOT_TOKEN_GENERIC_MESSAGE)


@router.post("/logout", status_code=204)
async def logout(redis: RedisDep, x_session_token: str | None = Header(None)) -> None:
    if x_session_token:
        await delete_session(redis, x_session_token)


@router.get("/me", response_model=MeOut)
async def me(current_user: CurrentUserDep, db: DbDep) -> MeOut:
    # Org.name/license fields folded into the same round trip via an outer
    # join instead of a trailing db.get(Org, ...) — this endpoint runs on
    # every dashboard hydration/reload, and DB here is a remote Neon
    # instance (see .env).
    result = await db.execute(
        select(
            OrgMembership,
            Org.name,
            Org.license_status,
            Org.license_expires_at,
            Org.enabled_features,
        )
        .outerjoin(Org, Org.id == OrgMembership.org_id)
        .where(OrgMembership.account_user_id == current_user.id)
        .order_by(OrgMembership.created_at)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=403, detail="Account has no org membership")
    membership, org_name, license_status, license_expires_at, enabled_features = row
    is_platform_org = membership.org_id == DEFAULT_ORG_ID
    if is_platform_org:
        license_status, license_expires_at, enabled_features = "active", None, None
    return MeOut(
        org_id=membership.org_id,
        org_name=org_name or "",
        role=membership.role,
        account_user_id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        is_superuser=current_user.is_superuser,
        is_platform_org=is_platform_org,
        license_status=license_status,
        license_expires_at=license_expires_at.isoformat() if license_expires_at else None,
        enabled_features=enabled_features,
    )
