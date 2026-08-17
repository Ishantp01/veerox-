"""Tests for apps.api.routers.appointments — slot conflicts and reminder
cancellation on cancel/reschedule.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models import Appointment, FollowUpTask, Lead, Org, User

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


async def _seed_org(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    await db.commit()


async def _seed_booked_lead(db: AsyncSession, scheduled_at: datetime) -> tuple[Appointment, FollowUpTask]:
    user = User(org_id=ORG_ID, phone="+910000000060", name="Caller")
    db.add(user)
    await db.flush()
    lead = Lead(org_id=ORG_ID, user_id=user.id, intent="booking", channel="whatsapp")
    db.add(lead)
    await db.flush()
    appointment = Appointment(org_id=ORG_ID, lead_id=lead.id, scheduled_at=scheduled_at)
    db.add(appointment)
    reminder = FollowUpTask(
        org_id=ORG_ID,
        lead_id=lead.id,
        rule_id=None,
        run_at=scheduled_at - timedelta(hours=1),
        status="pending",
        template_name="appointment_confirmation",
        template_params=["Caller", scheduled_at.date().isoformat(), "10:00"],
    )
    db.add(reminder)
    await db.commit()
    await db.refresh(appointment)
    await db.refresh(reminder)
    return appointment, reminder


async def test_cancelling_appointment_cancels_pending_reminder(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    scheduled_at = datetime.now(UTC) + timedelta(days=2)
    appointment, reminder = await _seed_booked_lead(db_session, scheduled_at)

    response = await client.patch(
        f"/appointments/{appointment.id}", json={"status": "cancelled"}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 200

    await db_session.refresh(reminder)
    assert reminder.status == "cancelled"


async def test_rescheduling_appointment_cancels_pending_reminder(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    scheduled_at = datetime.now(UTC) + timedelta(days=2)
    appointment, reminder = await _seed_booked_lead(db_session, scheduled_at)

    new_time = (scheduled_at + timedelta(hours=5)).isoformat()
    response = await client.patch(
        f"/appointments/{appointment.id}",
        json={"scheduled_at": new_time},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200

    await db_session.refresh(reminder)
    assert reminder.status == "cancelled"


async def test_updating_notes_only_leaves_reminder_pending(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A field-only update that doesn't touch scheduled_at/status must not
    disturb an already-scheduled reminder."""
    await _seed_org(db_session)
    scheduled_at = datetime.now(UTC) + timedelta(days=2)
    appointment, reminder = await _seed_booked_lead(db_session, scheduled_at)

    response = await client.patch(
        f"/appointments/{appointment.id}", json={"notes": "bring ID"}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 200

    await db_session.refresh(reminder)
    assert reminder.status == "pending"


async def test_create_appointment_rejects_slot_within_30_minutes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    scheduled_at = datetime.now(UTC) + timedelta(days=2)
    await client.post(
        "/appointments", json={"scheduled_at": scheduled_at.isoformat()}, headers=ADMIN_HEADERS
    )

    conflicting = (scheduled_at + timedelta(minutes=10)).isoformat()
    response = await client.post(
        "/appointments", json={"scheduled_at": conflicting}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 409
