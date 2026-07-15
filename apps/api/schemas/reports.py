from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from apps.api.schemas.campaign import CampaignCounts


class ReportsTimeseriesPoint(BaseModel):
    date: str
    calls: int
    whatsapp_messages: int
    leads_voice: int
    leads_whatsapp: int
    qualified_count: int
    usd_spend: float


class ReportsCampaignRow(BaseModel):
    id: UUID
    name: str
    channel: str
    status: str
    counts: CampaignCounts
    # completed targets that were also qualified; None when nothing has
    # resolved yet (no denominator).
    qualification_rate: float | None
