"""Transactional email via the Brevo (formerly Sendinblue) API.

Mirrors the httpx + structlog pattern used by ``channels/voice/plivo_client.py``:
a shared module-level ``httpx.AsyncClient`` for connection pooling, errors
logged then propagated to the caller (the route decides whether to swallow
or surface them).
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from apps.api.config import settings

logger = structlog.get_logger(__name__)

_http: httpx.AsyncClient = httpx.AsyncClient(timeout=10.0)

_BREVO_BASE = "https://api.brevo.com/v3"


def is_configured() -> bool:
    """True only when a Brevo API key is set — same convention as
    ``plivo_client.is_configured()`` gating a real send behind full config.
    """
    return bool(settings.brevo_api_key)


async def send_email(
    to_email: str,
    subject: str,
    html_content: str,
    to_name: str | None = None,
) -> dict[str, Any]:
    """Send a transactional email via ``POST /smtp/email``.

    Raises ``httpx.HTTPStatusError`` on a non-2xx response.
    """
    url = f"{_BREVO_BASE}/smtp/email"
    payload: dict[str, Any] = {
        "sender": {"name": settings.brevo_sender_name, "email": settings.brevo_sender_email},
        "to": [{"email": to_email, "name": to_name or to_email}],
        "subject": subject,
        "htmlContent": html_content,
    }
    try:
        r = await _http.post(
            url,
            json=payload,
            headers={"api-key": settings.brevo_api_key or "", "accept": "application/json"},
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "brevo_send_email_failed",
            to=to_email,
            error=str(exc),
            status=getattr(getattr(exc, "response", None), "status_code", None),
        )
        raise

    data: dict[str, Any] = r.json()
    logger.info("brevo_send_email_ok", to=to_email, message_id=data.get("messageId"))
    return data
