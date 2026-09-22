from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from apps.api.core.phone import normalize_phone
from apps.api.core.tools import get_or_create_lead_for_user
from apps.api.db.models import Lead, Org
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
    org_row = await db.get(Org, org_id)
    normalized_phone = (
        normalize_phone(payload.phone, org_row.default_country_code if org_row else None)
        if payload.phone
        else payload.phone
    )
    # One Lead per (org_id, user_id) now — see get_or_create_lead_for_user
    # and migrations/versions/b4c5d6e7f8a9. A caller POSTing for a user who
    # already has a lead updates it in place instead of erroring on the new
    # unique constraint; the fields below are explicit caller input, so they
    # override rather than fill-blank, same pattern as core/tools.py's
    # handlers.
    lead = await get_or_create_lead_for_user(
        db,
        org_id,
        payload.user_id,
        name=payload.name,
        phone=normalized_phone,
        intent=payload.intent,
    )
    lead.name = payload.name
    lead.phone = normalized_phone
    lead.intent = payload.intent
    lead.metadata_ = payload.metadata_
    await db.commit()
    await db.refresh(lead)
    return lead
