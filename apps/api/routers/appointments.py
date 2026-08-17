from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update

from apps.api.core.tools import _APPOINTMENT_TEMPLATE_NAME, find_conflicting_appointment
from apps.api.db.models import Appointment, FollowUpTask
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


@router.get("", response_model=list[AppointmentOut])
async def list_appointments(
    db: DbDep,
    org_id: RequestOrgDep,
    status: str | None = Query(None, pattern=_STATUS_PATTERN),
    starts_after: datetime | None = Query(None),
    starts_before: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[Appointment]:
    stmt = (
        select(Appointment)
        .where(Appointment.org_id == org_id)
        .order_by(Appointment.scheduled_at.asc())
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
    appointment = Appointment(
        org_id=org_id,
        contact_id=payload.contact_id,
        lead_id=payload.lead_id,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        assigned_user_id=payload.assigned_user_id,
        notes=payload.notes,
    )
    db.add(appointment)
    await db.commit()
    await db.refresh(appointment)
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
                FollowUpTask.template_name == _APPOINTMENT_TEMPLATE_NAME,
                FollowUpTask.status == "pending",
            )
            .values(status="cancelled")
        )

    for field, value in updates.items():
        setattr(appointment, field, value)
    await db.commit()
    await db.refresh(appointment)
    return appointment
