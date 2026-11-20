"""Find the one contact (``users`` row) for a phone number.

WhatsApp inbound stores ``919179609988`` while the dashboard, CRM and AI tools
store ``+919179609988``. Matching exactly on either form split one person into
two contacts — and so into two chats. Every lookup that can create a contact
goes through here so both spellings resolve to the same row.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models import User


def phone_forms(phone: str) -> list[str]:
    """Both spellings of a number: digits only, and with a leading ``+``."""
    digits = re.sub(r"\D", "", phone or "")
    return [digits, f"+{digits}"] if digits else [phone or ""]


async def find_user_by_phone(db: AsyncSession, org_id: UUID, phone: str) -> User | None:
    """The org's existing contact for ``phone`` in either spelling (oldest first
    if historical duplicates still exist)."""
    return (
        (
            await db.execute(
                select(User)
                .where(User.org_id == org_id, User.phone.in_(phone_forms(phone)))
                .order_by(User.created_at.asc(), User.id.asc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
