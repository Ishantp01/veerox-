from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base

# A durable record of a pending org/feature configuration change. A row is
# inserted transactionally alongside whatever owner-side change produced it
# (org creation, feature allocation, a license action) — see
# routers/platform.py and billing.py's license endpoints. Applied by the
# TARGET client deployment itself, which pulls its current configuration via
# an authenticated `GET /platform/sync/pending` and acknowledges via
# `POST /platform/sync/ack` (core/provisioning_apply.py) — the owner never
# pushes this anywhere; a row here is bookkeeping/audit for the owner
# panel's sync-status view, not a delivery queue.
SYNC_EVENT_TYPES = ("org_provision", "feature_sync", "license_update")
SYNC_EVENT_STATUSES = ("pending", "delivered", "failed")


class SyncEvent(Base):
    __tablename__ = "sync_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    deployment_id: Mapped[UUID] = mapped_column(
        ForeignKey("client_deployments.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Full provisioning payload as of the moment this event was queued
    # (central_org_ref, name, enabled_features, license snapshot,
    # config_version) — self-contained so the dispatcher never needs to
    # re-read current Org state for an old event (which could race with a
    # newer one already superseding it; see config_version below).
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Snapshot of ClientDeployment.config_version at enqueue time. The
    # client's /provisioning/apply rejects any payload whose config_version
    # is not strictly greater than what it already applied — the mechanism
    # that stops a slow/retried older event from overwriting a newer one
    # delivered out of order.
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
