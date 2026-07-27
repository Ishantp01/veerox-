"""Backfill script — creates one Contact per distinct (org_id, phone) found
in `leads` that don't already resolve to a Contact, then points those leads'
`contact_id` at it. Kept separate from the `655bab7b4adb` schema migration so
it can be re-run or rolled back independently of the schema change.

Run with:
    python scripts/backfill_contacts.py

Idempotent: re-running only touches leads still missing a contact_id, and
contact creation is INSERT ... ON CONFLICT DO NOTHING keyed on (org_id, phone).
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.config import settings


async def backfill() -> None:
    engine = create_async_engine(settings.database_url, echo=True)
    Session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        distinct_rows = (
            await session.execute(
                text(
                    "SELECT DISTINCT org_id, phone FROM leads "
                    "WHERE contact_id IS NULL AND phone IS NOT NULL"
                )
            )
        ).all()

        created = 0
        for org_id, phone in distinct_rows:
            contact_id = uuid.uuid4()
            result = await session.execute(
                text(
                    "INSERT INTO contacts (id, org_id, phone, created_at, updated_at) "
                    "VALUES (:id, :org_id, :phone, NOW(), NOW()) "
                    "ON CONFLICT (org_id, phone) DO NOTHING "
                    "RETURNING id"
                ),
                {"id": str(contact_id), "org_id": str(org_id), "phone": phone},
            )
            if result.first() is not None:
                created += 1

            await session.execute(
                text(
                    "UPDATE leads SET contact_id = ("
                    "  SELECT id FROM contacts WHERE org_id = :org_id AND phone = :phone"
                    ") WHERE org_id = :org_id AND phone = :phone AND contact_id IS NULL"
                ),
                {"org_id": str(org_id), "phone": phone},
            )

        await session.commit()

    await engine.dispose()
    print(f"Backfilled {len(distinct_rows)} distinct (org_id, phone) pairs, created {created} new contacts")


if __name__ == "__main__":
    asyncio.run(backfill())
