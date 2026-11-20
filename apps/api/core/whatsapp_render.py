"""What a customer actually sees for an outbound WhatsApp message.

A template send is stored as plain text so Chat can show the message the
customer received (header, body with the real values filled in, footer,
buttons) instead of a bare ``[template:name]`` marker. ``record_outbound_whatsapp``
lets the send paths that don't go through the dashboard (campaigns, follow-ups,
the AI's tools, appointment confirmations) leave a record in the lead's thread.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select

logger = structlog.get_logger(__name__)

_PLACEHOLDER = re.compile(r"\{\{\s*(\d+)\s*\}\}")
# The default label the template form gives an unnamed slot ("Param 1") says nothing.
_GENERIC_LABEL = re.compile(r"^\s*(param|variable|var)\s*\d*\s*$", re.IGNORECASE)
LEGACY_TEMPLATE_MARKER = re.compile(r"^\[template:(.+)\]$")


def _fill(text: str, params: Sequence[str] | None, labels: Sequence[str] | None = None) -> str:
    """Replace {{1}}, {{2}}, ... with the values sent. A value that wasn't kept
    (rows saved before sends stored their text) shows the template's own label
    for that slot, e.g. ``[name]`` (or ``…`` if it has no real label), rather than a raw ``{{1}}``."""
    values = list(params or [])
    names = list(labels or [])

    def sub(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        if 0 <= index < len(values) and values[index] is not None:
            return str(values[index])
        label = names[index] if 0 <= index < len(names) else ""
        if label and not _GENERIC_LABEL.match(label):
            return f"[{label}]"
        return "…"

    return _PLACEHOLDER.sub(sub, text)


def render_template_message(
    template: Any | None,
    template_name: str,
    body_params: Sequence[str] | None = None,
    header_params: Sequence[str] | None = None,
) -> str:
    """The customer-facing text of a template send.

    ``template`` is the org's saved ``WhatsAppTemplate`` row (None if it was
    never saved locally). Without a saved body there is nothing to show but the
    template's name.
    """
    body = (getattr(template, "body_preview", None) or "").strip()
    if not body:
        return f"[Template: {template_name}]"

    parts: list[str] = []
    header_type = (getattr(template, "header_type", None) or "").upper()
    if header_type == "TEXT" and getattr(template, "header_text", None):
        parts.append(_fill(template.header_text, header_params))
    elif header_type in {"IMAGE", "VIDEO", "DOCUMENT"}:
        parts.append(f"[{header_type.title()}]")
    parts.append(_fill(body, body_params, getattr(template, "param_labels", None)))
    footer = getattr(template, "footer_text", None)
    if footer:
        parts.append(footer)
    buttons = getattr(template, "buttons", None) or []
    labels = [b.get("text") for b in buttons if isinstance(b, dict) and b.get("text")]
    if labels:
        parts.append("\n".join(f"[ {label} ]" for label in labels))
    return "\n\n".join(parts)


async def record_outbound_template(
    org_id: UUID | None,
    phone: str,
    template_name: str,
    body_params: Sequence[str] | None = None,
    header_params: Sequence[str] | None = None,
) -> None:
    """``record_outbound_whatsapp`` for a template send: looks up the org's saved
    template and records the rendered text. Never raises."""
    if org_id is None:
        return
    try:
        from apps.api.db.models import WhatsAppTemplate
        from apps.api.db.session import AsyncSessionLocal  # lazy: avoid import cycle

        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(WhatsAppTemplate).where(
                        WhatsAppTemplate.org_id == org_id, WhatsAppTemplate.name == template_name
                    )
                )
            ).scalars().first()
        content = render_template_message(row, template_name, body_params, header_params)
    except Exception:  # noqa: BLE001
        logger.warning("outbound_whatsapp_record_failed", exc_info=True)
        return
    await record_outbound_whatsapp(org_id, phone, content)


async def record_outbound_whatsapp(org_id: UUID | None, phone: str, content: str) -> None:
    """Add a sent WhatsApp message to the recipient's thread. Best effort: opens
    its own session and never raises — a bookkeeping failure must not fail a
    send that already went out. A recipient who has no ``User`` row yet (never
    messaged, never imported) is skipped rather than created."""
    if org_id is None or not phone or not content:
        return
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return
    try:
        from apps.api.core.chat_threads import get_or_create_whatsapp_conversation
        from apps.api.db.models import Message, User
        from apps.api.db.session import AsyncSessionLocal  # lazy: avoid import cycle

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(
                    select(User).where(User.org_id == org_id, User.phone.in_([digits, f"+{digits}"]))
                )
            ).scalars().first()
            if user is None:
                return
            conversation = await get_or_create_whatsapp_conversation(db, org_id, user.id)
            db.add(
                Message(
                    org_id=org_id,
                    conversation_id=conversation.id,
                    user_id=user.id,
                    role="assistant",
                    content=content,
                    channel="whatsapp",
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("outbound_whatsapp_record_failed", exc_info=True)
