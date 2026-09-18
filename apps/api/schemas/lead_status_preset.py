from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LeadStatusPresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    created_at: datetime


class LeadStatusPresetCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=20)
