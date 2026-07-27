from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from apps.api.core.tools import _default_org_id
from apps.api.db.models import Lead
from apps.api.deps import DbDep
from apps.api.schemas.lead import LEAD_QUALIFICATION_STATUSES, LEAD_STATUSES
from apps.api.schemas.sales import (
    ChannelBreakdown,
    PipelineOut,
    PipelineStage,
    QualificationFunnelStage,
    RevenueSummaryOut,
)

router = APIRouter(prefix="/sales", tags=["sales"])


@router.get("/pipeline", response_model=PipelineOut)
async def get_pipeline(db: DbDep) -> PipelineOut:
    """Lead count + $ value per CRM status — aggregated from the existing
    `Lead.status`/`Lead.deal_value` columns rather than a separate Deal
    entity (see db/models/lead.py's `deal_value` comment)."""
    org_id = _default_org_id()
    rows = (
        await db.execute(
            select(Lead.status, func.count(), func.coalesce(func.sum(Lead.deal_value), 0))
            .where(Lead.org_id == org_id)
            .group_by(Lead.status)
        )
    ).all()
    by_status = {status: (count, float(value)) for status, count, value in rows}

    stages = [
        PipelineStage(status=status, count=by_status.get(status, (0, 0.0))[0], value=by_status.get(status, (0, 0.0))[1])
        for status in LEAD_STATUSES
    ]
    total_value = sum(stage.value for stage in stages)
    return PipelineOut(stages=stages, total_value=total_value)


@router.get("/revenue-summary", response_model=RevenueSummaryOut)
async def get_revenue_summary(db: DbDep) -> RevenueSummaryOut:
    org_id = _default_org_id()

    pipeline_value_row = (
        await db.execute(
            select(func.coalesce(func.sum(Lead.deal_value), 0)).where(
                Lead.org_id == org_id, Lead.status != "lost"
            )
        )
    ).one()
    won_value_row = (
        await db.execute(
            select(func.coalesce(func.sum(Lead.deal_value), 0)).where(
                Lead.org_id == org_id, Lead.status == "converted"
            )
        )
    ).one()

    channel_rows = (
        await db.execute(
            select(Lead.channel, func.count(), func.coalesce(func.sum(Lead.deal_value), 0))
            .where(Lead.org_id == org_id)
            .group_by(Lead.channel)
        )
    ).all()
    by_channel = [
        ChannelBreakdown(channel=channel or "unknown", count=count, value=float(value))
        for channel, count, value in channel_rows
    ]

    qual_rows = (
        await db.execute(
            select(Lead.qualification_status, func.count())
            .where(Lead.org_id == org_id)
            .group_by(Lead.qualification_status)
        )
    ).all()
    qual_by_status = dict(qual_rows)
    qualification_funnel = [
        QualificationFunnelStage(qualification_status=status, count=qual_by_status.get(status, 0))
        for status in LEAD_QUALIFICATION_STATUSES
    ]

    return RevenueSummaryOut(
        total_pipeline_value=float(pipeline_value_row[0]),
        won_value=float(won_value_row[0]),
        by_channel=by_channel,
        qualification_funnel=qualification_funnel,
    )
