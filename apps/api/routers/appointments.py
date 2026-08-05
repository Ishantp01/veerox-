from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from apps.api.db.models import Appointment
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
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(appointment, field, value)
    await db.commit()
    await db.refresh(appointment)
    return appointment
