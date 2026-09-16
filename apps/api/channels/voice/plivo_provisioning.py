"""Best-effort registration of an org's dedicated Plivo numbers' inbound
Answer URL — see ``plivo_client.py::register_inbound_answer_url``.

Now that Plivo credentials are per-org (no more single platform-wide
account), there's no single startup call that can register "the" number any
more. Instead:

- ``register_all_org_plivo_numbers`` runs once at app startup, best-effort,
  for every org that has both its own Plivo credentials AND at least one
  dedicated Plivo number.
- ``register_org_plivo_numbers`` is the per-org version, fired as a
  fire-and-forget background task whenever an org's Plivo credentials are
  saved (routers/admin.py's PUT /settings/plivo-credentials) or its phone
  numbers are replaced (routers/auth.py::provision_org,
  routers/billing.py::update_org, routers/admin.py::update_org_numbers) —
  so a newly added Plivo number gets its answer URL registered without a
  redeploy.

Both swallow all exceptions — a Plivo API hiccup here must never block
startup or a settings save.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import plivo_client
from apps.api.core.org_credentials import resolve_plivo_credentials
from apps.api.db.models.org import Org
from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)


async def register_org_plivo_numbers(db: AsyncSession, org_id: UUID | str) -> None:
    """Register the inbound Answer URL for every dedicated Plivo number this
    org has, using this org's own Plivo credentials. No-op (logs and
    returns) when the org has no Plivo credentials configured, or no
    dedicated Plivo numbers — nothing to register in either case."""
    try:
        org_record = await db.get(Org, org_id)
        creds = resolve_plivo_credentials(org_record)
        if creds is None:
            return
        numbers = (
            await db.execute(
                select(OrgPhoneNumber.phone_number).where(
                    OrgPhoneNumber.org_id == org_id,
                    OrgPhoneNumber.provider == "plivo",
                )
            )
        ).scalars().all()
        for digits in numbers:
            try:
                await plivo_client.register_inbound_answer_url(creds, f"+{digits}")
            except Exception:
                logger.warning(
                    "plivo_org_number_registration_failed",
                    org_id=str(org_id),
                    number=digits,
                    exc_info=True,
                )
    except Exception:
        logger.warning("plivo_org_registration_failed", org_id=str(org_id), exc_info=True)


def fire_and_forget_register_org_plivo_numbers(org_id: UUID | str) -> None:
    """Schedule ``register_org_plivo_numbers`` as a background task without
    blocking or letting its exceptions propagate to the caller.

    Opens its own DB session rather than reusing the caller's request-scoped
    one, since this task is still running (Plivo API calls can take a
    while) well after the request that triggered it has returned and its
    session has been closed.
    """

    async def _run() -> None:
        try:
            async with AsyncSessionLocal() as db:
                await register_org_plivo_numbers(db, org_id)
        except Exception:
            logger.warning("plivo_org_registration_task_failed", org_id=str(org_id), exc_info=True)

    asyncio.create_task(_run())


async def register_all_org_plivo_numbers(db: AsyncSession) -> None:
    """Best-effort startup pass: for every org with its own Plivo
    credentials configured, register the inbound Answer URL for each of its
    dedicated Plivo numbers. Never raises — swallows and logs per-org
    failures so one bad org can't block startup or other orgs."""
    try:
        org_ids = (await db.execute(select(Org.id))).scalars().all()
    except Exception:
        logger.warning("plivo_startup_registration_org_list_failed", exc_info=True)
        return

    for org_id in org_ids:
        try:
            await register_org_plivo_numbers(db, org_id)
        except Exception:
            logger.warning(
                "plivo_startup_registration_failed", org_id=str(org_id), exc_info=True
            )
