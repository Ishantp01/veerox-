from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from apps.api.db.models import Contact
from apps.api.deps import DbDep, RequestOrgDep, verify_admin_or_session
from apps.api.schemas.crm import ContactCreate, ContactOut, ContactUpdateIn, ContactWithLeadsOut

router = APIRouter(prefix="/crm", tags=["crm"], dependencies=[Depends(verify_admin_or_session)])


@router.get("/contacts", response_model=list[ContactOut])
async def list_contacts(
    db: DbDep,
    org_id: RequestOrgDep,
    q: str | None = Query(None, description="Filter by name/phone substring"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[Contact]:
    stmt = select(Contact).where(Contact.org_id == org_id).order_by(Contact.created_at.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Contact.name.ilike(like)) | (Contact.phone.ilike(like)))
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/contacts", response_model=ContactOut, status_code=201)
async def create_contact(payload: ContactCreate, db: DbDep, org_id: RequestOrgDep) -> Contact:
    contact = Contact(
        org_id=org_id,
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        company=payload.company,
        tags=payload.tags,
        owner_user_id=payload.owner_user_id,
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return contact


@router.get("/contacts/{contact_id}", response_model=ContactWithLeadsOut)
async def get_contact(contact_id: UUID, db: DbDep, org_id: RequestOrgDep) -> Contact:
    stmt = (
        select(Contact)
        .where(Contact.id == contact_id, Contact.org_id == org_id)
        .options(selectinload(Contact.leads))
    )
    result = await db.execute(stmt)
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: UUID, payload: ContactUpdateIn, db: DbDep, org_id: RequestOrgDep
) -> Contact:
    stmt = select(Contact).where(Contact.id == contact_id, Contact.org_id == org_id)
    contact = (await db.execute(stmt)).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    await db.commit()
    await db.refresh(contact)
    return contact
