from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base

# Fixed vocabulary for `UsageEvent.service` — see req §4. Kept as a plain
# string column (matches the rest of the codebase's status-column
# convention, e.g. Lead.status) rather than a Postgres native enum, so
# adding a new service later is a code-only change, no migration.
USAGE_SERVICES = (
    "whatsapp",
    "voice",
    "ai",
    "compute",
    "database",
    "storage",
    "network",
    "queue",
    "other",
)

# Fixed vocabulary for `UsageEvent.usage_type`.
USAGE_TYPES = (
    "whatsapp_messages",
    "voice_seconds",
    "ai_input_tokens",
    "ai_output_tokens",
    "ai_request_count",
    "worker_job_seconds",
    "cpu_ms",
    "memory_mb_ms",
    "storage_gb_month",
    "bandwidth_gb",
    "database_operations",
    "invocation_count",
)


class UsageEvent(Base):
    """One immutable, billable/measurable occurrence — the source-of-truth
    ledger every other usage/billing table is derived from.

    Never updated or deleted after insert; a correction to an already-
    aggregated period goes through `UsageAdjustment` instead (see
    db/models/usage_adjustment.py). `event_id` is caller-supplied and must be
    deterministic for anything that can retry (a call, a webhook delivery, a
    worker tick) — see core/usage.py::deterministic_event_id — so a retry
    that calls `core/usage.py::record_usage` again with the same `event_id`
    is silently ignored by the unique constraint below rather than double-
    counted.

    `metadata_json` must never hold API keys, tokens, message bodies, call
    audio, or other sensitive/unnecessary payloads — `record_usage` rejects
    a denylisted key defensively, but callers are the first line of defense.
    """

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_org_created", "org_id", "created_at"),
        Index(
            "ix_usage_events_org_service_type_created",
            "org_id",
            "service",
            "usage_type",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Globally unique, deterministic for retryable operations — the
    # idempotency key. Not the primary key so `id` stays a plain surrogate
    # key like every other table's.
    event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    # Correlates several usage events (e.g. AI tokens + voice seconds) back
    # to one call/conversation/webhook delivery. Not a DB foreign key —
    # deliberately loose since "request" spans multiple entity types
    # (call_uuid, WhatsApp message id, HTTP request id, ...).
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    service: Mapped[str] = mapped_column(String(20), nullable=False)
    usage_type: Mapped[str] = mapped_column(String(40), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    # Third-party provider this usage was billed by (openai/plivo/twilio/
    # meta), or NULL for pure AWS-infrastructure usage (compute/database/...)
    # that isn't attributable to any external provider.
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Where this event was recorded from (e.g. "voice_webhook",
    # "whatsapp_adapter", "campaign_dialer") — free text, for audit/debugging,
    # not a foreign key.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Non-sensitive context only (e.g. {"model": "gpt-4o-mini", "campaign_id": "..."}).
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Immutable — no `updated_at`. A correction goes through UsageAdjustment,
    # never by mutating this row.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
