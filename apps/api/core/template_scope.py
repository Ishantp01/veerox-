"""Which WhatsApp templates a WhatsApp number may use.

A template lives on a WABA (a WhatsApp Business Account), not on a phone number, and a number belongs to one WABA
(``OrgPhoneNumber.waba_id``; NULL on a number or a template = the org's default WABA). Meta rejects a send whose template is
not on the sending number's WABA, so a campaign is checked up front and gets a clear message instead.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.org_credentials import resolve_meta_credentials
from apps.api.db.models import Org, OrgPhoneNumber, WhatsAppTemplate


async def ensure_template_matches_number(
    db: AsyncSession, org_id: UUID, number: OrgPhoneNumber | None, template_name: str | None
) -> None:
    """Raise a 400 when ``template_name`` is known to this org but only on a different WABA than ``number``'s. Does nothing
    when it cannot tell (no number or template given, the WABA unknown, or the template not saved locally)."""
    if number is None or not template_name:
        return
    creds = resolve_meta_credentials(await db.get(Org, org_id))
    default_waba = creds.business_account_id if creds else None
    number_waba = number.waba_id or default_waba
    if not number_waba:
        return
    stamps = (
        (
            await db.execute(
                select(WhatsAppTemplate.waba_id).where(
                    WhatsAppTemplate.org_id == org_id,
                    WhatsAppTemplate.name == template_name,
                    WhatsAppTemplate.active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if not stamps:
        return
    if number_waba not in {stamp or default_waba for stamp in stamps}:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The template '{template_name}' is on a different WhatsApp Business Account than the number you picked. "
                "Choose a template from that number's own account."
            ),
        )
