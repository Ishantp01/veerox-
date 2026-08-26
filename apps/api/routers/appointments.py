from __future__ import annotations

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update

from apps.api.core.tools import (
    _APPOINTMENT_REMINDER_TEMPLATE_NAME,
    _get_or_create_user_by_phone,
    _normalize_phone,
    DEFAULT_BOOKING_TIMEZONE,
    find_conflicting_appointment,
    schedule_appointment_reminders,
    send_appointment_confirmation,
)
from apps.api.db.models import Appointment, Contact, FollowUpTask, Lead
from apps.api.deps import DbDep, RequestOrgDep, verify_admin_or_session
from apps.api.schemas.appointment import (
    APPOINTMENT_STATUSES,
    AppointmentCreate,
    AppointmentOut,
    AppointmentUpdateIn,
)

router = APIRouter(
    prefix="/appointments", tags=["appointments"], dependencies=[Depends(verify_admin_or_session)]
)

_STATUS_PATTERN = f"^({'|'.join(APPOINTMENT_STATUSES)})$"
_SORT_PATTERN = "^(newest|oldest)$"


@router.get("", response_model=list[AppointmentOut])
async def list_appointments(
    db: DbDep,
    org_id: RequestOrgDep,
    status: str | None = Query(None, pattern=_STATUS_PATTERN),
    starts_after: datetime | None = Query(None),
    starts_before: datetime | None = Query(None),
    sort: str = Query("newest", pattern=_SORT_PATTERN),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[Appointment]:
    order_by = (
        Appointment.created_at.desc()
        if sort == "newest"
        else Appointment.created_at.asc()
    )
    stmt = (
        select(Appointment)
        .where(Appointment.org_id == org_id)
        .order_by(order_by)
    )
    if status:
        stmt = stmt.where(Appointment.status == status)
    if starts_after:
        stmt = stmt.where(Appointment.scheduled_at >= starts_after)
    if starts_before:
        stmt = stmt.where(Appointment.scheduled_at <= starts_before)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=AppointmentOut, status_code=201)
async def create_appointment(
    payload: AppointmentCreate, db: DbDep, org_id: RequestOrgDep
) -> Appointment:
    conflict = await find_conflicting_appointment(db, org_id, payload.scheduled_at)
    if conflict is not None:
        raise HTTPException(
            status_code=409, detail="Another appointment is scheduled too close to this time"
        )

    # Resolve who to notify (reminders + immediate confirmation, below) from
    # whichever of contact_id/lead_id the caller supplied. A contact with no
    # Lead yet gets one created here (intent="booking", channel="dashboard")
    # so it has somewhere to attach the reminder FollowUpTasks — mirroring
    # what core.tools.book_appointment does for AI-driven bookings, so a
    # manually-booked appointment notifies the customer identically.
    lead_id = payload.lead_id
    notify_phone: str | None = None
    notify_name: str | None = None
    if lead_id is not None:
        lead = await db.get(Lead, lead_id)
        if lead is None or lead.org_id != org_id:
            raise HTTPException(status_code=404, detail="Lead not found")
        notify_phone = lead.phone
        notify_name = lead.name
    elif payload.contact_id is not None:
        contact = await db.get(Contact, payload.contact_id)
        if contact is None or contact.org_id != org_id:
            raise HTTPException(status_code=404, detail="Contact not found")
        user = await _get_or_create_user_by_phone(
            db, org_id=org_id, phone=contact.phone, name=contact.name
        )
        new_lead = Lead(
            org_id=org_id,
            user_id=user.id,
            contact_id=contact.id,
            name=contact.name,
            phone=_normalize_phone(contact.phone),
            intent="booking",
            channel="dashboard",
        )
        db.add(new_lead)
        await db.flush()  # populate new_lead.id for the Appointment FK below
        lead_id = new_lead.id
        notify_phone = contact.phone
        notify_name = contact.name

    appointment = Appointment(
        org_id=org_id,
        contact_id=payload.contact_id,
        lead_id=lead_id,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        assigned_user_id=payload.assigned_user_id,
        notes=payload.notes,
    )
    db.add(appointment)

    local_dt = payload.scheduled_at.astimezone(ZoneInfo(DEFAULT_BOOKING_TIMEZONE))
    date_str = local_dt.date().isoformat()
    time_str = local_dt.strftime("%H:%M")
    if lead_id is not None:
        schedule_appointment_reminders(
            db, org_id, lead_id, notify_name, date_str, time_str, payload.scheduled_at
        )

    await db.commit()
    await db.refresh(appointment)

    if lead_id is not None:
        await send_appointment_confirmation(
            db, org_id, appointment.id, notify_phone, notify_name, date_str, time_str
        )

    return appointment


@router.get("/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(appointment_id: UUID, db: DbDep, org_id: RequestOrgDep) -> Appointment:
    stmt = select(Appointment).where(
        Appointment.id == appointment_id, Appointment.org_id == org_id
    )
    appointment = (await db.execute(stmt)).scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appointment


@router.patch("/{appointment_id}", response_model=AppointmentOut)
async def update_appointment(
    appointment_id: UUID, payload: AppointmentUpdateIn, db: DbDep, org_id: RequestOrgDep
) -> Appointment:
    stmt = select(Appointment).where(
        Appointment.id == appointment_id, Appointment.org_id == org_id
    )
    appointment = (await db.execute(stmt)).scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    updates = payload.model_dump(exclude_unset=True)
    if "scheduled_at" in updates:
        conflict = await find_conflicting_appointment(
            db, org_id, updates["scheduled_at"], exclude_appointment_id=appointment.id
        )
        if conflict is not None:
            raise HTTPException(
                status_code=409, detail="Another appointment is scheduled too close to this time"
            )

    # A rescheduled or cancelled appointment must not let a stale
    # pre-scheduled reminder (see book_appointment in core/tools.py) fire for
    # the old time. Rescheduling doesn't re-create reminders for the new
    # time — only bookings made through book_appointment get them.
    if appointment.lead_id is not None and (
        "scheduled_at" in updates or updates.get("status") in {"cancelled", "no_show"}
    ):
        await db.execute(
            update(FollowUpTask)
            .where(
                FollowUpTask.lead_id == appointment.lead_id,
                FollowUpTask.template_name == _APPOINTMENT_REMINDER_TEMPLATE_NAME,
                FollowUpTask.status == "pending",
            )
            .values(status="cancelled")
        )

    for field, value in updates.items():
        setattr(appointment, field, value)
    await db.commit()
    await db.refresh(appointment)
    return appointment
