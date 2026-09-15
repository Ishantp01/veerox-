from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# One org's library of reusable qualification-criteria presets, picked from a
# dropdown when creating a campaign (see schemas/campaign.py) instead of
# retyping the bar every time.


class QualificationCriteriaPresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    criteria_text: str
    created_at: datetime
    updated_at: datetime


class QualificationCriteriaPresetCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    criteria_text: str = Field(..., min_length=1, max_length=5000)


class QualificationCriteriaPresetUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    criteria_text: str | None = Field(None, min_length=1, max_length=5000)
