from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from apps.api.schemas.conversation import ConversationSummaryOut

# Kept in sync with the CHECK-free `status` column on Lead (apps/api/db/models/lead.py)
# — enforced here rather than a DB constraint so adding a stage doesn't need a migration.
LEAD_STATUSES: tuple[str, ...] = ("new", "contacted", "qualified", "converted", "lost")
LeadStatus = Literal["new", "contacted", "qualified", "converted", "lost"]


class LeadCreate(BaseModel):
    org_id: UUID
    user_id: UUID
    name: str | None = None
    phone: str | None = None
    intent: str | None = None
    channel: str | None = None
    metadata_: dict | None = None


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    user_id: UUID
    name: str | None
    phone: str | None
    intent: str | None
    channel: str | None
    metadata_: dict | None
    status: str
    follow_up_at: datetime | None
    follow_up_note: str | None
    created_at: datetime


class LeadDetailOut(LeadOut):
    """LeadOut plus that lead's conversation history, for the lead detail page
    (joined via the shared user_id — Lead has no direct FK to Conversation)."""

    conversations: list[ConversationSummaryOut]


class LeadUpdateIn(BaseModel):
    """Partial update for lead status / follow-up. Only fields explicitly set
    by the caller are applied — see admin.update_lead's use of
    model_dump(exclude_unset=True), so omitting follow_up_at/note leaves them
    untouched while explicitly passing `null` clears them."""

    status: LeadStatus | None = None
    follow_up_at: datetime | None = None
    follow_up_note: str | None = None


class LeadImportRow(BaseModel):
    """One row of a programmatic (JSON) bulk-lead import — the API-driven
    counterpart to a CSV/Excel row uploaded via ``POST /admin/leads/import``."""

    phone: str
    name: str | None = None
    intent: str | None = None


class LeadBulkImportIn(BaseModel):
    """Rows are staged as an auto-generated campaign (see
    ``admin._create_campaign_from_rows``), not written to the CRM directly —
    only prospects the AI qualifies as interested become ``Lead`` rows."""

    leads: list[LeadImportRow]
    # Which worker reaches out to the resulting campaign targets. Defaults to
    # "voice" (in the endpoint) when omitted.
    channel: str | None = None
    campaign_name: str | None = None
    criteria: str | None = None
