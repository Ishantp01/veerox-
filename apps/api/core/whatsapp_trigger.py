"""``send_whatsapp_details``: send the caller the demo booking link, pricing or feature overview over WhatsApp, using
our own WhatsApp service (``send_whatsapp_message``, then an approved template when the 24-hour window is closed).

The content of each message comes from settings (``WHATSAPP_DETAILS_DEMO_LINK`` / ``_PRICING`` / ``_FEATURES``). The tool
is offered to the voice agent only when at least one is set. The result is the real outcome of the send, so the agent
can say "sent" only after status ok. UNVERIFIED on a live number: a call never opens the 24-hour window, so the first
send to a new caller normally needs ``WHATSAPP_DETAILS_TEMPLATE`` (an approved template with ONE body variable that
carries the text).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings

logger = structlog.get_logger(__name__)

INTEREST_TYPES = ("demo_link", "pricing", "features")

SEND_WHATSAPP_DETAILS_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "send_whatsapp_details",
    "description": (
        "Send the caller a ready-made WhatsApp message with the demo booking link, the pricing details or the "
        "feature overview. Call it only after the caller has asked for that (or said yes when you offered it). "
        "It goes to the number they are calling from unless they name another one. Say it is sent only after the "
        "result says status ok."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "interest_type": {"type": "string", "enum": list(INTEREST_TYPES), "description": "What to send."},
            "phone_number": {
                "type": "string",
                "description": "Only if the caller named a different number (E.164 preferred). Omit for their own.",
            },
        },
        "required": ["interest_type"],
    },
}


def _content() -> dict[str, str | None]:
    return {
        "demo_link": settings.whatsapp_details_demo_link,
        "pricing": settings.whatsapp_details_pricing,
        "features": settings.whatsapp_details_features,
    }


def is_enabled() -> bool:
    return any(_content().values())


async def send_whatsapp_details(
    db: AsyncSession,
    interest_type: str = "",
    phone_number: str | None = None,
    user_id: UUID | None = None,
    org_id: UUID | None = None,
    **_: Any,
) -> dict[str, Any]:
    from apps.api.core import tools  # lazy: tools imports this module for its dispatch table

    if interest_type not in INTEREST_TYPES:
        return {"status": "error", "reason": "unknown_interest_type", "allowed": list(INTEREST_TYPES)}
    message = _content().get(interest_type)
    if not message:
        return {"status": "error", "reason": "nothing_configured_to_send", "interest_type": interest_type}
    result = await tools.send_whatsapp_message(
        db, message=message, phone=phone_number, user_id=user_id, org_id=org_id, channel="voice"
    )
    if result.get("status") != "ok" and result.get("reason") == "outside_24h_window" and settings.whatsapp_details_template:
        result = await tools.send_whatsapp_template(
            db, name=settings.whatsapp_details_template, body_params=[message], phone=phone_number,
            user_id=user_id, org_id=org_id,
        )
    if result.get("status") == "ok":
        logger.info("whatsapp_details_sent", interest=interest_type)
        return {"status": "ok", "interest_type": interest_type}
    logger.warning("whatsapp_details_not_sent", interest=interest_type, reason=result.get("reason"))
    return {
        "status": "error", "reason": result.get("reason", "send_failed"),
        "note": "Not sent. Tell the caller it did not go through; offer to read the details out loud instead.",
    }
