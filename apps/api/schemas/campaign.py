from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

CampaignStatus = Literal["running", "paused", "completed"]
TargetStatus = Literal["pending", "calling", "completed", "failed"]
CampaignChannel = Literal["voice", "whatsapp"]


class CampaignTargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    name: str | None
    phone: str
    status: str
    qualified: bool | None
    disposition_reason: str | None
    attempt_count: int
    conversation_id: UUID | None
    created_at: datetime
    called_at: datetime | None


class CampaignCounts(BaseModel):
    pending: int
    calling: int
    completed: int
    failed: int
    qualified: int


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    criteria: str
    channel: str
    status: str
    created_at: datetime
    counts: CampaignCounts


class CampaignDetailOut(CampaignOut):
    targets: list[CampaignTargetOut]


class CampaignCreateResult(BaseModel):
    campaign: CampaignOut
    imported: int
    skipped: int
    errors: list[dict[str, str | int]]


class CampaignStatusUpdateOut(BaseModel):
    id: UUID
    status: str
