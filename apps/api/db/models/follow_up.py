from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class FollowUpRule(Base):
    """A standing rule that spawns FollowUpTasks for leads matching its
    trigger — evaluated each dispatcher tick, alongside the built-in
    ``Lead.follow_up_at`` trigger every lead already supports without a rule.
    """

    __tablename__ = "follow_up_rules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Only "status_change" today — matches leads whose `status` equals
    # trigger_config["status"], scheduling one task per lead
    # trigger_config["delay_hours"] hours out. New trigger types append here,
    # each with its own trigger_config shape, rather than a schema migration.
    trigger_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="status_change")
    trigger_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, server_default="whatsapp")
    message_template: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class FollowUpTask(Base):
    """One scheduled follow-up send, either spawned from a Lead's own
    ``follow_up_at``/``follow_up_note`` (``rule_id`` is null) or from a
    ``FollowUpRule`` match.
    """

    __tablename__ = "follow_up_tasks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("follow_up_rules.id", ondelete="SET NULL"), nullable=True
    )
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # pending -> sending (claimed atomically by the dispatcher, see
    # workers/follow_up_dispatcher.py._claim_due_tasks) -> sent | failed |
    # skipped (channel doesn't support auto-send) | cancelled (operator
    # cancelled before it ran, only possible from "pending").
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
