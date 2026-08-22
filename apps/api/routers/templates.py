from __future__ import annotations

import asyncio
import json
import re
import structlog
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from apps.api.channels.whatsapp import client as wa_client
from apps.api.core.tools import _default_org_id
from apps.api.db.models import WhatsAppTemplate
from apps.api.deps import DbDep, RedisDep, verify_admin_or_session
from apps.api.schemas.template import TemplateCreate, TemplateOut, TemplateSyncResult, TemplateUpdateIn

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["templates"], dependencies=[Depends(verify_admin_or_session)])

# Meta review status (pending/approved/rejected) changes on the order of
# hours, not seconds — caching it briefly avoids hitting the Graph API on
# every dashboard page load/poll without ever showing meaningfully stale data.
_META_STATUS_CACHE_KEY = "veerox:cache:wa_template_status"
_META_STATUS_CACHE_TTL_SECS = 60


@router.get("/whatsapp-templates", response_model=list[TemplateOut])
async def list_templates(
    db: DbDep,
    redis: RedisDep,
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

    # Best-effort: match each saved row to its live Meta review status by
    # name+language. A failure here (Meta down, bad creds) shouldn't break
    # the page — rows just render with no status badge. Run concurrently
    # with the DB query — separate connections (Postgres vs. Meta's Graph
    # API over httpx), nothing shared to serialize on, and the Meta call is
    # the slower of the two, so this halves the endpoint's latency instead
    # of paying for both one after the other.
    async def _load_meta_status() -> dict[str, str]:
        cached = await redis.get(_META_STATUS_CACHE_KEY)
        if cached is not None:
            return json.loads(cached)
        try:
            status_by_key = {
                f"{t['name']} {t['language']}": t.get("status", "")
                for t in await wa_client.list_templates()
                if t.get("name") and t.get("language")
            }
        except httpx.HTTPError as exc:
            logger.warning("whatsapp_templates_status_fetch_failed", error=str(exc))
            return {}
        await redis.set(
            _META_STATUS_CACHE_KEY, json.dumps(status_by_key), ex=_META_STATUS_CACHE_TTL_SECS
        )
        return status_by_key

    db_result, status_by_key = await asyncio.gather(db.execute(stmt), _load_meta_status())
    templates = list(db_result.scalars().all())

    return [
        TemplateOut.model_validate(t).model_copy(
            update={"meta_status": status_by_key.get(f"{t.name} {t.language}")}
        )
        for t in templates
    ]


_VALID_META_CATEGORIES = {"MARKETING", "UTILITY", "AUTHENTICATION"}


@router.post("/whatsapp-templates", response_model=TemplateOut, status_code=201)
async def create_template(payload: TemplateCreate, db: DbDep) -> WhatsAppTemplate:
    """Create a local template row — and, when ``body_preview`` (the actual
    template body) is given, submit it to Meta for review first.

    Submitting requires a valid category and, per placeholder, an example
    value — ``param_labels`` doubles as that example list (Meta only needs
    *an* example per ``{{n}}``, not a human label, so whatever's typed in
    those boxes is sent as-is). A campaign still can't use the template
    until Meta approves it (``meta_status`` flips PENDING -> APPROVED on the
    list endpoint) — this only registers it for review.

    Leaving ``body_preview`` blank skips Meta entirely and just saves a
    local row, for cataloging a template that already exists on the WABA
    without re-submitting it (the ``/whatsapp-templates/sync`` endpoint is
    the better way to do that now, but this is kept for a manual/offline
    entry).
    """
    if payload.body_preview:
        category = (payload.category or "UTILITY").strip().upper()
        if category not in _VALID_META_CATEGORIES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"category must be one of {', '.join(sorted(_VALID_META_CATEGORIES))} "
                    "to submit a template to Meta"
                ),
            )
        try:
            await wa_client.create_template(
                name=payload.name,
                body_text=payload.body_preview,
                category=category,
                language_code=payload.language,
                example_params=[p for p in payload.param_labels if p.strip()] or None,
            )
        except httpx.HTTPStatusError as exc:
            message = wa_client.friendly_error_message(wa_client._meta_error_detail(exc))
            raise HTTPException(
                status_code=400, detail=f"Meta rejected this template: {message}"
            ) from exc

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


_BODY_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")


def _body_component(meta_template: dict) -> dict | None:
    for component in meta_template.get("components") or []:
        if component.get("type") == "BODY":
            return component
    return None


@router.post("/whatsapp-templates/sync", response_model=TemplateSyncResult)
async def sync_templates_from_meta(db: DbDep) -> TemplateSyncResult:
    """Pull every template that exists on the WABA in Meta and add a local
    row for any that don't have one yet (matched by name+language).

    Templates only ever get made directly in Meta Business Manager — this
    app has no "submit to Meta" flow — so without this, a template created
    there is invisible on the WhatsApp Templates page (and unusable in a
    campaign) until someone manually re-types its name/language/params here.
    Existing local rows are left untouched (their ``param_labels`` are
    hand-edited to be human-readable, e.g. "Name"/"Date" — Meta only gives us
    an example value, not a label, so we never overwrite what's there).
    """
    org_id = _default_org_id()
    meta_templates = await wa_client.list_templates()

    existing = (
        (
            await db.execute(
                select(WhatsAppTemplate.name, WhatsAppTemplate.language).where(
                    WhatsAppTemplate.org_id == org_id
                )
            )
        )
        .all()
    )
    existing_keys = {(name, language) for name, language in existing}

    created: list[WhatsAppTemplate] = []
    for meta_template in meta_templates:
        name = meta_template.get("name")
        language = meta_template.get("language")
        if not name or not language or (name, language) in existing_keys:
            continue

        body = _body_component(meta_template)
        body_text = body.get("text") if body else None
        placeholder_count = (
            len(set(_BODY_PLACEHOLDER_RE.findall(body_text))) if body_text else 0
        )
        category = meta_template.get("category")

        template = WhatsAppTemplate(
            org_id=org_id,
            name=name,
            language=language,
            category=category.title() if category else None,
            param_labels=[f"Param {i}" for i in range(1, placeholder_count + 1)],
            body_preview=body_text,
            active=True,
        )
        db.add(template)
        created.append(template)
        existing_keys.add((name, language))

    if created:
        await db.commit()
        for template in created:
            await db.refresh(template)

    return TemplateSyncResult(
        created=[TemplateOut.model_validate(t) for t in created],
        skipped=len(meta_templates) - len(created),
        total_on_meta=len(meta_templates),
    )


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
