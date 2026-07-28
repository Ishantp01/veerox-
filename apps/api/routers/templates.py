from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from apps.api.core.tools import _default_org_id
from apps.api.db.models import WhatsAppTemplate
from apps.api.deps import DbDep
from apps.api.schemas.template import TemplateCreate, TemplateOut, TemplateUpdateIn

router = APIRouter(tags=["templates"])


@router.get("/whatsapp-templates", response_model=list[TemplateOut])
async def list_templates(
    db: DbDep,
    active: bool | None = Query(None),
) -> list[WhatsAppTemplate]:
    org_id = _default_org_id()
    stmt = (
        select(WhatsAppTemplate)
        .where(WhatsAppTemplate.org_id == org_id)
        .order_by(WhatsAppTemplate.created_at.desc())
    )
    if active is not None:
        stmt = stmt.where(WhatsAppTemplate.active == active)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/whatsapp-templates", response_model=TemplateOut, status_code=201)
async def create_template(payload: TemplateCreate, db: DbDep) -> WhatsAppTemplate:
    template = WhatsAppTemplate(
        org_id=_default_org_id(),
        name=payload.name,
        language=payload.language,
        category=payload.category,
        param_labels=payload.param_labels,
        body_preview=payload.body_preview,
        active=payload.active,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.patch("/whatsapp-templates/{template_id}", response_model=TemplateOut)
async def update_template(template_id: UUID, payload: TemplateUpdateIn, db: DbDep) -> WhatsAppTemplate:
    template = await db.get(WhatsAppTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(template, field, value)
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/whatsapp-templates/{template_id}")
async def delete_template(template_id: UUID, db: DbDep) -> dict[str, bool]:
    template = await db.get(WhatsAppTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.delete(template)
    await db.commit()
    return {"ok": True}
