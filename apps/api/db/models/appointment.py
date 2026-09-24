from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    # One of contact_id/lead_id is expected to be set — whichever the booking
    # was made from. Both nullable since a booking may only have one at hand.
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    # Indexed: without it, deleting a lead makes Postgres seq-scan this table
    # to null out the FK (ON DELETE SET NULL), which gets slow as it grows.
    lead_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="scheduled")
    # Set when the lead asked to be called back at a specific time (e.g. "I'm
    # busy, call me tomorrow at 5") instead of booking a real appointment —
    # extracted by the request_callback tool (core/tools.py) during a live
    # call/WhatsApp chat. Independent of scheduled_at, which stays the actual
    # appointment slot when one exists.
    callback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set by workers/follow_up_dispatcher.py once it has actually placed the
    # outbound callback call for this row — distinct from callback_at (the
    # requested time) so a due callback is claimed exactly once instead of
    # being re-dialed on every dispatcher poll tick. Null means "not called
    # back yet" regardless of whether callback_at is in the past.
    callback_dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assigned_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
