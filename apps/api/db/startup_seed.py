from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import hash_token
from apps.api.db.models.account_user import AccountUser
from apps.api.db.models.org import Org
from apps.api.db.models.org_membership import OrgMembership
from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)

DEFAULT_OWNER_ID = UUID("00000000-0000-0000-0000-0000000000a1")
DEFAULT_OWNER_EMAIL = "owner@veerox-admin.com"


def _digits_only(phone_number: str | None) -> str | None:
    digits = re.sub(r"\D", "", phone_number or "")
    return digits or None


async def _seed_env_phone_number(
    db: AsyncSession,
    *,
    org_id: UUID,
    provider: str,
    raw_phone_number: str | None,
) -> bool:
    phone_number = _digits_only(raw_phone_number)
    if not phone_number:
        return False

    existing = (
        await db.execute(
            select(OrgPhoneNumber).where(
                OrgPhoneNumber.provider == provider,
                OrgPhoneNumber.phone_number == phone_number,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.org_id != org_id:
            logger.warning(
                "startup_seed_phone_number_owned_by_other_org",
                provider=provider,
                phone_number=phone_number,
                owning_org_id=str(existing.org_id),
                default_org_id=str(org_id),
            )
        return False

    has_default = (
        await db.execute(
            select(OrgPhoneNumber.id)
            .where(
                OrgPhoneNumber.org_id == org_id,
                OrgPhoneNumber.provider == provider,
                OrgPhoneNumber.is_default.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    max_position = (
        await db.execute(
            select(func.max(OrgPhoneNumber.position)).where(
                OrgPhoneNumber.org_id == org_id,
                OrgPhoneNumber.provider == provider,
            )
        )
    ).scalar_one_or_none()

    db.add(
        OrgPhoneNumber(
            org_id=org_id,
            provider=provider,
            phone_number=phone_number,
            is_default=has_default is None,
            position=(max_position or -1) + 1,
        )
    )
    return True


async def ensure_env_seed_data(db: AsyncSession) -> None:
    """Ensure the env-defined owner org/admin rows exist after a DB swap.

    This is deliberately idempotent and conservative: it creates missing
    bootstrap rows and fills blank env-owned fields, but it does not overwrite
    org settings that an operator already configured in the dashboard.
    """
    org_id = UUID(settings.default_org_id)

    org = await db.get(Org, org_id)
    created_org = False
    if org is None:
        org = Org(
            id=org_id,
            name="Demo Org",
            whatsapp_phone_number_id=(settings.meta_phone_number_id or "").strip() or None,
        )
        db.add(org)
        created_org = True
    elif not org.whatsapp_phone_number_id and settings.meta_phone_number_id:
        env_phone_number_id = settings.meta_phone_number_id.strip()
        owner = (
            await db.execute(
                select(Org.id).where(
                    Org.whatsapp_phone_number_id == env_phone_number_id,
                    Org.id != org_id,
                )
            )
        ).scalar_one_or_none()
        if owner is None:
            org.whatsapp_phone_number_id = env_phone_number_id
        else:
            logger.warning(
                "startup_seed_whatsapp_phone_number_owned_by_other_org",
                whatsapp_phone_number_id=env_phone_number_id,
                owning_org_id=str(owner),
                default_org_id=str(org_id),
            )

    account_user = await db.get(AccountUser, DEFAULT_OWNER_ID)
    created_owner = False
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
        created_owner = True
    else:
        token_hash = hash_token(settings.admin_token)
        if account_user.token_hash != token_hash:
            account_user.token_hash = token_hash
        account_user.is_active = True
        account_user.is_superuser = True

    membership = (
        await db.execute(
            select(OrgMembership).where(
                OrgMembership.org_id == org_id,
                OrgMembership.account_user_id == DEFAULT_OWNER_ID,
            )
        )
    ).scalar_one_or_none()
    created_membership = False
    if membership is None:
        db.add(
            OrgMembership(
                org_id=org_id,
                account_user_id=DEFAULT_OWNER_ID,
                role="admin",
                joined_at=datetime.now(UTC),
            )
        )
        created_membership = True

    seeded_plivo = await _seed_env_phone_number(
        db,
        org_id=org_id,
        provider="plivo",
        raw_phone_number=settings.plivo_phone_number,
    )
    seeded_twilio = await _seed_env_phone_number(
        db,
        org_id=org_id,
        provider="twilio",
        raw_phone_number=settings.twilio_phone_number,
    )

    await db.commit()
    logger.info(
        "startup_seed_env_data_done",
        default_org_id=str(org_id),
        created_org=created_org,
        created_owner=created_owner,
        created_membership=created_membership,
        has_whatsapp_phone_number_id=bool(org.whatsapp_phone_number_id),
        seeded_plivo_phone_number=seeded_plivo,
        seeded_twilio_phone_number=seeded_twilio,
    )


async def ensure_env_seed_data_on_startup() -> None:
    try:
        async with AsyncSessionLocal() as db:
            await ensure_env_seed_data(db)
    except Exception:
        logger.exception("startup_seed_env_data_failed")
        if settings.startup_seed_required:
            raise
