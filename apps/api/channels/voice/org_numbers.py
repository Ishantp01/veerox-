"""Reads/writes for an org's OrgPhoneNumber rows — the dedicated Plivo/Twilio
numbers it dials from and can be reached on. Shared by every write path
(routers/auth.py::provision_org, routers/billing.py::update_org,
routers/admin.py::update_org_numbers) and every outbound-calling entry point
(routers/admin.py::outbound_call, workers/follow_up_dispatcher.py,
core/tools.py's AI callback tool — workers/campaign_dialer.py is the one
exception, since its claim query needs these joined in-line for performance,
see its own docstring).
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.schemas.org_numbers import OrgPhoneNumberIn


async def get_default_numbers(db: AsyncSession, org_id: UUID | str) -> tuple[str | None, str | None]:
    """(plivo_from_e164, twilio_from_e164) — this org's *default* number per
    provider, or None if it has none on that provider. Re-prefixes the
    digits-only storage with "+" to match what the provider APIs expect,
    same as the old direct Org.plivo_phone_number/twilio_phone_number reads."""
    rows = (
        await db.execute(
            select(OrgPhoneNumber.provider, OrgPhoneNumber.phone_number).where(
                OrgPhoneNumber.org_id == org_id, OrgPhoneNumber.is_default.is_(True)
            )
        )
    ).all()
    by_provider = {provider: number for provider, number in rows}
    plivo = by_provider.get("plivo")
    twilio = by_provider.get("twilio")
    return (f"+{plivo}" if plivo else None, f"+{twilio}" if twilio else None)


async def replace_org_phone_numbers(
    db: AsyncSession, org_id: UUID | str, numbers: list[OrgPhoneNumberIn]
) -> None:
    """Stage a full replace of this org's number rows onto `db` — deletes its
    existing rows and re-adds `numbers`. Caller commits (and catches
    IntegrityError for a 409 when a number is already owned by another org),
    same as every other field on these endpoints.

    Exactly one row per provider ends up is_default=True: the first entry
    for that provider explicitly marked is_default, or — if none are — the
    first entry for that provider in submission order.
    """
    await db.execute(delete(OrgPhoneNumber).where(OrgPhoneNumber.org_id == org_id))

    default_index: dict[str, int] = {}
    for i, entry in enumerate(numbers):
        if entry.is_default and entry.provider not in default_index:
            default_index[entry.provider] = i
    for i, entry in enumerate(numbers):
        default_index.setdefault(entry.provider, i)

    for i, entry in enumerate(numbers):
        db.add(
            OrgPhoneNumber(
                org_id=org_id,
                provider=entry.provider,
                phone_number=re.sub(r"\D", "", entry.phone_number),
                is_default=default_index.get(entry.provider) == i,
            )
        )
