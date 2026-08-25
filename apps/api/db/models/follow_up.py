from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, Text, func, text
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
    # Nullable: a rule may be template-only (see template_name below) instead
    # of free text.
    message_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When set, materialized tasks send via this pre-approved Meta template
    # instead of (or, if message_template is also set, is used for) the
    # free-text path — see workers/follow_up_dispatcher.py's _execute_task,
    # which already has this exact branch for appointment-reminder tasks.
    template_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Ordered per-placeholder tokens for template_name's {{1}}, {{2}}, ...
    # body — same "{{contact_name}}"/"{{send_date}}"/"{{send_time}}" tokens
    # (or a literal fixed value) that workers/whatsapp_dispatcher.py resolves
    # for campaigns; workers/follow_up_dispatcher.py resolves these the same
    # way, fresh at send time, using the matched lead's name.
    template_params: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
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
    __table_args__ = (
        # Prevents duplicate tasks if two dispatcher instances race under
        # horizontal scaling (see workers/follow_up_dispatcher.py's
        # materialize functions and longrunning/operations/load.md) — a
        # no-op constraint for a single-instance deployment, which never
        # produces the concurrent insert these guard against.
        #
        # template_name IS NULL matters on the first index: core/tools.py's
        # book_appointment creates several rule_id=NULL tasks per lead on
        # purpose (one per reminder offset), always with template_name set
        # — a bare "one rule_id IS NULL row per lead" constraint would
        # break that feature. Confirmed against production data before
        # adding this (see migration e7b3a5c9f2d4's docstring).
        Index(
            "uq_follow_up_tasks_lead_builtin",
            "lead_id",
            unique=True,
            postgresql_where=text("rule_id IS NULL AND template_name IS NULL"),
            sqlite_where=text("rule_id IS NULL AND template_name IS NULL"),
        ),
        Index(
            "uq_follow_up_tasks_lead_rule",
            "lead_id",
            "rule_id",
            unique=True,
            postgresql_where=text("rule_id IS NOT NULL"),
            sqlite_where=text("rule_id IS NOT NULL"),
        ),
    )

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
    # When set, the dispatcher sends via this Meta pre-approved WhatsApp
    # template (ordered body params in template_params) instead of the
    # rule/lead free-text path — see workers/follow_up_dispatcher.py's
    # _execute_task. Used by appointment reminders, which must reach the
    # recipient even outside the 24h free-form-text session window.
    template_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    template_params: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
