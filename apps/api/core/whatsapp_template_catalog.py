"""Org WhatsApp template catalog — lookup + prompt helpers.

A leaf module (imports only the model) shared by the agent tool
(``core/tools.py::send_whatsapp_template``) and both system-prompt builders
(``core/agent.py`` and ``channels/voice/realtime_bridge.py``), so the agent
knows which approved templates it can send — including their header/button
structure — and can resolve the one it names, mirroring
``core/whatsapp_assets.py``'s file catalog.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.template import WhatsAppTemplate


async def load_active_templates(db: AsyncSession, org_id: UUID) -> list[WhatsAppTemplate]:
    result = await db.execute(
        select(WhatsAppTemplate)
        .where(WhatsAppTemplate.org_id == org_id, WhatsAppTemplate.active.is_(True))
        .order_by(WhatsAppTemplate.name)
    )
    return list(result.scalars().all())


async def resolve_template(
    db: AsyncSession, org_id: UUID, name: str
) -> WhatsAppTemplate | list[str] | None:
    """Find the active template the agent named — exact case-insensitive
    match only (unlike file names, template names are already machine-safe
    identifiers, so no fuzzy/substring match is needed)."""
    wanted = (name or "").strip()
    if not wanted:
        return None
    matches = (
        (
            await db.execute(
                select(WhatsAppTemplate).where(
                    WhatsAppTemplate.org_id == org_id,
                    WhatsAppTemplate.active.is_(True),
                    func.lower(WhatsAppTemplate.name) == wanted.lower(),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return [t.name for t in matches]
    return None


async def template_catalog_prompt_block(db: AsyncSession, org_id: UUID) -> str:
    """A short system-prompt block listing the templates the agent may send,
    or ``""`` when the org has none active. Only local metadata is used
    (Meta's live PENDING/APPROVED/REJECTED status isn't checked here — an
    unapproved template just fails the send and the tool reports that)."""
    templates = await load_active_templates(db, org_id)
    if not templates:
        return ""
    lines = [
        "Approved WhatsApp templates you can send with the "
        "send_whatsapp_template tool (pass the name exactly as written here) "
        "— use one of these instead of send_whatsapp_message whenever the "
        "contact has no open 24h WhatsApp session, or the business asks for "
        "a specific approved flow (e.g. an offer with a coupon button):"
    ]
    for t in templates:
        parts = [f'- "{t.name}" ({t.language})']
        if t.body_preview:
            parts.append(f'body: "{t.body_preview}"')
        if t.param_labels:
            parts.append(f"body params in order: {', '.join(t.param_labels)}")
        if t.header_type in ("IMAGE", "VIDEO", "DOCUMENT"):
            parts.append(
                f"header: {t.header_type} — REQUIRED every send, pass header_param as a "
                "saved file name or https:// URL"
            )
        elif t.header_type and t.header_text:
            has_var = "{{1}}" in t.header_text
            parts.append(
                f'header: "{t.header_text}"' + (" (has a {{1}} variable)" if has_var else "")
            )
        if t.footer_text:
            parts.append(f'footer: "{t.footer_text}"')
        if t.buttons:
            button_bits = []
            for i, b in enumerate(t.buttons):
                btype = b.get("type")
                if btype == "URL" and "{{1}}" in (b.get("url") or ""):
                    button_bits.append(f"[{i}] dynamic URL button \"{b.get('text')}\"")
                elif btype == "COPY_CODE":
                    button_bits.append(f"[{i}] copy-code button")
                elif btype == "QUICK_REPLY":
                    button_bits.append(f"[{i}] quick reply \"{b.get('text')}\"")
                elif btype == "PHONE_NUMBER":
                    button_bits.append(f"[{i}] call button \"{b.get('text')}\"")
            if button_bits:
                parts.append("buttons: " + "; ".join(button_bits))
        lines.append(" — ".join(parts))
    lines.append(
        "Only send a template that's actually relevant to what the contact asked for or "
        "the situation calls for. Never name a template that isn't in this list."
    )
    return "\n".join(lines)
