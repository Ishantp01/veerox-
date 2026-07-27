from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

APPOINTMENT_STATUSES: tuple[str, ...] = (
    "scheduled",
    "confirmed",
    "completed",
    "cancelled",
    "no_show",
)
AppointmentStatus = Literal["scheduled", "confirmed", "completed", "cancelled", "no_show"]


class AppointmentCreate(BaseModel):
    contact_id: UUID | None = None
    lead_id: UUID | None = None
    scheduled_at: datetime
    duration_minutes: int = 30
    assigned_user_id: UUID | None = None
    notes: str | None = None


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    contact_id: UUID | None
    lead_id: UUID | None
    scheduled_at: datetime
    duration_minutes: int
    status: AppointmentStatus
    assigned_user_id: UUID | None
    notes: str | None
    created_at: datetime


class AppointmentUpdateIn(BaseModel):
    """Partial update — only fields explicitly set by the caller are applied."""

    scheduled_at: datetime | None = None
    duration_minutes: int | None = None
    status: AppointmentStatus | None = None
    assigned_user_id: UUID | None = None
    notes: str | None = None
