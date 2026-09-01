"""Tests for apps.api.routers.appointments — slot conflicts and reminder
cancellation on cancel/reschedule.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core import tools
from apps.api.db.models import Appointment, Contact, FollowUpTask, Lead, Org, User

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
        template_name="appointment_reminder",
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


async def test_create_appointment_with_contact_notifies_and_schedules_reminder(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Booking from the CRM's New Appointment dialog (contact_id, no lead_id)
    must notify the customer identically to an AI-driven booking: a Lead is
    created to hang the reminder off, reminder FollowUpTasks are queued, and
    an immediate WhatsApp confirmation is sent."""
    sent: list[tuple[str, str, list[str] | None]] = []

    async def _fake_send_template(
        to_e164: str, template_name: str, body_params: list[str] | None = None, **kwargs: object
    ) -> dict[str, object]:
        sent.append((to_e164, template_name, body_params))
        return {}

    monkeypatch.setattr(tools.wa_client, "send_template", _fake_send_template)
    await _seed_org(db_session)
    contact = Contact(org_id=ORG_ID, name="Asha", phone="+910000000099")
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)

    scheduled_at = datetime.now(UTC) + timedelta(days=2)
    response = await client.post(
        "/appointments",
        json={"contact_id": str(contact.id), "scheduled_at": scheduled_at.isoformat()},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["contact_id"] == str(contact.id)
    assert body["lead_id"] is not None
    assert body["name"] == "Asha"
    assert body["phone"] == "+910000000099"

    lead_stmt = select(Lead).where(Lead.id == uuid.UUID(body["lead_id"]))
    lead = (await db_session.execute(lead_stmt)).scalar_one()
    assert lead.contact_id == contact.id
    assert lead.phone == "+910000000099"

    reminder_stmt = select(FollowUpTask).where(FollowUpTask.lead_id == lead.id)
    reminders = (await db_session.execute(reminder_stmt)).scalars().all()
    assert len(reminders) == 3
    assert all(r.template_name == "appointment_reminder" for r in reminders)

    assert len(sent) == 1
    assert sent[0][0] == "+910000000099"
    assert sent[0][1] == "appointment_confirmation"


async def test_list_and_get_appointment_surface_name_and_phone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Both the list and single-item endpoints must resolve name/phone from
    the appointment's Lead — not just contact_id/lead_id UUIDs — since the
    dashboard table shows who the appointment is for (see AppointmentOut)."""
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000061", name="Rahul")
    db_session.add(user)
    await db_session.flush()
    lead = Lead(
        org_id=ORG_ID,
        user_id=user.id,
        name="Rahul",
        phone="+910000000061",
        intent="booking",
        channel="whatsapp",
    )
    db_session.add(lead)
    await db_session.flush()
    appointment = Appointment(
        org_id=ORG_ID, lead_id=lead.id, scheduled_at=datetime.now(UTC) + timedelta(days=2)
    )
    db_session.add(appointment)
    await db_session.commit()

    list_response = await client.get("/appointments", headers=ADMIN_HEADERS)
    assert list_response.status_code == 200
    [row] = [r for r in list_response.json() if r["id"] == str(appointment.id)]
    assert row["name"] == "Rahul"
    assert row["phone"] == "+910000000061"

    get_response = await client.get(f"/appointments/{appointment.id}", headers=ADMIN_HEADERS)
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["name"] == "Rahul"
    assert body["phone"] == "+910000000061"


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
