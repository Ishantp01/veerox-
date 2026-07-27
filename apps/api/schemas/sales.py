from __future__ import annotations

from pydantic import BaseModel


class PipelineStage(BaseModel):
    status: str
    count: int
    value: float


class PipelineOut(BaseModel):
    stages: list[PipelineStage]
    total_value: float


class ChannelBreakdown(BaseModel):
    channel: str
    count: int
    value: float


class QualificationFunnelStage(BaseModel):
    qualification_status: str
    count: int


class RevenueSummaryOut(BaseModel):
    total_pipeline_value: float
    won_value: float
    by_channel: list[ChannelBreakdown]
    qualification_funnel: list[QualificationFunnelStage]
