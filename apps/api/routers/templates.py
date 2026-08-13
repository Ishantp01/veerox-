from __future__ import annotations

import structlog
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from apps.api.channels.whatsapp import client as wa_client
from apps.api.core.tools import _default_org_id
from apps.api.db.models import WhatsAppTemplate
from apps.api.deps import DbDep, verify_admin_or_session
from apps.api.schemas.template import TemplateCreate, TemplateOut, TemplateUpdateIn

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["templates"], dependencies=[Depends(verify_admin_or_session)])


@router.get("/whatsapp-templates", response_model=list[TemplateOut])
async def list_templates(
    db: DbDep,
    active: bool | None = Query(None),
) -> list[TemplateOut]:
    org_id = _default_org_id()
    stmt = (
        select(WhatsAppTemplate)
        .where(WhatsAppTemplate.org_id == org_id)
        .order_by(WhatsAppTemplate.created_at.desc())
    )
    if active is not None:
        stmt = stmt.where(WhatsAppTemplate.active == active)
    result = await db.execute(stmt)
    templates = list(result.scalars().all())

    # Best-effort: match each saved row to its live Meta review status by
    # name+language. A failure here (Meta down, bad creds) shouldn't break
    # the page — rows just render with no status badge.
    status_by_key: dict[tuple[str, str], str] = {}
    try:
        for t in await wa_client.list_templates():
            name, language = t.get("name"), t.get("language")
            if name and language:
                status_by_key[(name, language)] = t.get("status", "")
    except httpx.HTTPError as exc:
        logger.warning("whatsapp_templates_status_fetch_failed", error=str(exc))

    return [
        TemplateOut.model_validate(t).model_copy(
            update={"meta_status": status_by_key.get((t.name, t.language))}
        )
        for t in templates
    ]


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
