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
from apps.api.core.org_credentials import resolve_meta_credentials
from apps.api.core.tools import _default_org_id
from apps.api.core.whatsapp_assets import asset_public_url
from apps.api.db.models import Org, WhatsAppAsset, WhatsAppTemplate
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
    org_record = await db.get(Org, org_id)
    meta_creds = resolve_meta_credentials(org_record)
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
        if meta_creds is None or not meta_creds.business_account_id:
            return {}
        cached = await redis.get(_META_STATUS_CACHE_KEY)
        if cached is not None:
            return json.loads(cached)
        try:
            status_by_key = {
                f"{t['name']} {t['language']}": t.get("status", "")
                for t in await wa_client.list_templates(
                    meta_creds.access_token, meta_creds.business_account_id
                )
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
_VALID_BUTTON_TYPES = {"QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE"}


def _meta_buttons(payload: TemplateCreate) -> list[dict] | None:
    if not payload.buttons:
        return None
    result: list[dict] = []
    for btn in payload.buttons:
        if btn.type not in _VALID_BUTTON_TYPES:
            raise HTTPException(status_code=400, detail=f"Unknown button type '{btn.type}'")
        entry: dict = {"type": btn.type}
        if btn.type == "QUICK_REPLY":
            entry["text"] = btn.text
        elif btn.type == "URL":
            entry["text"] = btn.text
            entry["url"] = btn.url
            if btn.example:
                entry["example"] = [btn.example]
        elif btn.type == "PHONE_NUMBER":
            entry["text"] = btn.text
            entry["phone_number"] = btn.phone_number
        elif btn.type == "COPY_CODE":
            entry["example"] = btn.example
        result.append(entry)
    return result


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
    org_id = _default_org_id()
    org_record = await db.get(Org, org_id)
    meta_creds = resolve_meta_credentials(org_record)
    media_header_types = {"IMAGE", "VIDEO", "DOCUMENT"}
    header_type = (payload.header_type or "").upper() or None
    header_handle: str | None = None
    # For a media header, the send form's default URL (reused from the TEXT
    # header's header_example field, see routers/templates.py header docs)
    # is filled in from the uploaded asset's own public URL.
    header_example = payload.header_example

    # Validate the category (when submitting to Meta) before doing the media
    # upload below — an invalid category shouldn't burn an upload call to
    # Meta only to reject the request a few lines later.
    category: str | None = None
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

    if header_type in media_header_types:
        if not payload.header_asset_id:
            raise HTTPException(
                status_code=400,
                detail=f"header_asset_id is required for a {header_type} header",
            )
        if meta_creds is None:
            raise HTTPException(
                status_code=400, detail="This org's Meta WhatsApp App isn't configured yet."
            )
        asset = await db.get(WhatsAppAsset, payload.header_asset_id)
        if asset is None or asset.org_id != org_id:
            raise HTTPException(status_code=404, detail="Header asset not found")
        try:
            header_handle = await wa_client.get_header_media_handle(
                meta_creds.access_token,
                meta_creds.app_id,
                data=asset.data,
                mime_type=asset.mime_type,
                filename=asset.filename,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            # Broader than HTTPStatusError on purpose — a timeout or dropped
            # connection mid-upload (httpx.TimeoutException/ConnectError) is
            # just as real a failure mode here as a 4xx from Meta, and both
            # should surface as a friendly 400 instead of an unhandled 500.
            message = wa_client.friendly_error_message(wa_client._meta_error_detail(exc))
            raise HTTPException(
                status_code=400, detail=f"Couldn't upload header media to Meta. {message}"
            ) from exc
        header_example = asset_public_url(asset)

    if payload.body_preview:
        if meta_creds is None or not meta_creds.business_account_id:
            raise HTTPException(
                status_code=400, detail="This org's Meta WhatsApp App isn't configured yet."
            )
        try:
            await wa_client.create_template(
                meta_creds.access_token,
                meta_creds.business_account_id,
                name=payload.name,
                body_text=payload.body_preview,
                category=category,
                language_code=payload.language,
                example_params=[p for p in payload.param_labels if p.strip()] or None,
                header_type=header_type,
                header_text=payload.header_text or None,
                header_example=payload.header_example or None,
                header_handle=header_handle,
                footer_text=payload.footer_text or None,
                buttons=_meta_buttons(payload),
            )
        except httpx.HTTPStatusError as exc:
            message = wa_client.friendly_error_message(wa_client._meta_error_detail(exc))
            # friendly_error_message already turns known error codes into plain
            # language, but WhatsApp's own top-level message is sometimes just
            # a generic label ("Invalid parameter") with no useful detail
            # attached — showing that verbatim to a non-technical client gives
            # them nothing to act on. The single most common real cause of this
            # generic rejection is a variable sitting right at the start/end of
            # the message with no real words around it, so name that concretely
            # instead of a vague "double-check" instruction.
            if message.strip().lower() in {"invalid parameter", "invalid parameters", "message couldn't be sent."}:
                message = (
                    'Add words before and after each blank — e.g. "Hi {{1}}, thanks '
                    'for contacting us!" instead of just "Hi {{1}}". Then try again.'
                )
            raise HTTPException(status_code=400, detail=f"Couldn't create this template. {message}") from exc

    template = WhatsAppTemplate(
        org_id=org_id,
        name=payload.name,
        language=payload.language,
        category=payload.category,
        param_labels=payload.param_labels,
        body_preview=payload.body_preview,
        header_type=header_type,
        header_text=payload.header_text,
        header_example=header_example,
        footer_text=payload.footer_text,
        buttons=[b.model_dump(exclude_none=True) for b in payload.buttons],
        active=payload.active,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


_BODY_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")


def _component(meta_template: dict, component_type: str) -> dict | None:
    for component in meta_template.get("components") or []:
        if component.get("type") == component_type:
            return component
    return None


def _body_component(meta_template: dict) -> dict | None:
    return _component(meta_template, "BODY")


def _normalize_meta_buttons(raw_buttons: list[dict]) -> list[dict]:
    """Meta's own button shape uses a list for ``example`` (URL/COPY_CODE);
    our TemplateButton schema stores a single string — flatten it so synced
    rows validate against TemplateOut the same as locally-created ones."""
    normalized: list[dict] = []
    for btn in raw_buttons:
        example = btn.get("example")
        if isinstance(example, list):
            example = example[0] if example else None
        normalized.append(
            {
                "type": btn.get("type"),
                "text": btn.get("text"),
                "url": btn.get("url"),
                "phone_number": btn.get("phone_number"),
                "example": example,
            }
        )
    return normalized


@router.post("/whatsapp-templates/sync", response_model=TemplateSyncResult)
async def sync_templates_from_meta(db: DbDep) -> TemplateSyncResult:
    """Pull every template that exists on the WABA in Meta: add a local row
    for any that don't have one yet (matched by name+language), and backfill
    HEADER/FOOTER/BUTTONS structure onto existing rows that are missing it.

    Templates only ever get made directly in Meta Business Manager — this
    app has no "submit to Meta" flow — so without this, a template created
    there is invisible on the WhatsApp Templates page (and unusable in a
    campaign) until someone manually re-types its name/language/params here.

    Existing rows only get their header_type/header_text/footer_text/buttons
    filled in when locally EMPTY — never overwritten once set. A row created
    before this app tracked those fields (or before its Meta template had a
    header/footer/buttons attached) would otherwise stay permanently stale
    and every send of it would fail with Meta's "Format mismatch, expected
    IMAGE, received UNKNOWN" error. ``name``/``language``/``category``/
    ``param_labels``/``body_preview`` are left alone on existing rows either
    way — ``param_labels`` in particular are hand-edited to be human-readable
    (e.g. "Name"/"Date"), and Meta only ever gives us an example value, not a
    label, so overwriting those would make them worse.
    """
    org_id = _default_org_id()
    org_record = await db.get(Org, org_id)
    meta_creds = resolve_meta_credentials(org_record)
    if meta_creds is None or not meta_creds.business_account_id:
        raise HTTPException(status_code=400, detail="This org's Meta WhatsApp App isn't configured yet.")
    meta_templates = await wa_client.list_templates(meta_creds.access_token, meta_creds.business_account_id)

    existing_rows = (
        (
            await db.execute(
                select(WhatsAppTemplate).where(WhatsAppTemplate.org_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    existing_by_key = {(t.name, t.language): t for t in existing_rows}

    created: list[WhatsAppTemplate] = []
    updated: list[WhatsAppTemplate] = []
    for meta_template in meta_templates:
        name = meta_template.get("name")
        language = meta_template.get("language")
        if not name or not language:
            continue

        header = _component(meta_template, "HEADER")
        footer = _component(meta_template, "FOOTER")
        buttons_component = _component(meta_template, "BUTTONS")
        meta_buttons = (
            _normalize_meta_buttons(buttons_component.get("buttons", []))
            if buttons_component
            else []
        )

        existing = existing_by_key.get((name, language))
        if existing is not None:
            changed = False
            if not existing.header_type and header:
                existing.header_type = header.get("format")
                existing.header_text = header.get("text")
                changed = True
            if not existing.footer_text and footer:
                existing.footer_text = footer.get("text")
                changed = True
            if not existing.buttons and meta_buttons:
                existing.buttons = meta_buttons
                changed = True
            if changed:
                updated.append(existing)
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
            header_type=header.get("format") if header else None,
            header_text=header.get("text") if header else None,
            footer_text=footer.get("text") if footer else None,
            buttons=meta_buttons,
            active=True,
        )
        db.add(template)
        created.append(template)
        existing_by_key[(name, language)] = template

    if created or updated:
        await db.commit()
        for template in created + updated:
            await db.refresh(template)

    return TemplateSyncResult(
        created=[TemplateOut.model_validate(t) for t in created],
        updated=[TemplateOut.model_validate(t) for t in updated],
        skipped=len(meta_templates) - len(created) - len(updated),
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
