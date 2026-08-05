from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from apps.api.db.models import Lead
from apps.api.deps import DbDep, RequestOrgDep, verify_admin_or_session
from apps.api.schemas.lead import LeadCreate, LeadOut

router = APIRouter(
    prefix="/leads", tags=["leads"], dependencies=[Depends(verify_admin_or_session)]
)


@router.get("", response_model=list[LeadOut])
async def list_leads(
    db: DbDep,
    intent: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[Lead]:
    stmt = select(Lead).order_by(Lead.created_at.desc()).limit(limit).offset(offset)
    if intent:
        stmt = stmt.where(Lead.intent == intent)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=LeadOut, status_code=201)
async def create_lead(payload: LeadCreate, db: DbDep, org_id: RequestOrgDep) -> Lead:
    lead = Lead(
        org_id=org_id,
        user_id=payload.user_id,
        name=payload.name,
        phone=payload.phone,
        intent=payload.intent,
        metadata_=payload.metadata_,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return lead
