"""Outbound call client for the Plivo Voice API.

Mirrors the httpx + structlog pattern used by ``channels/whatsapp/client.py``:
a shared module-level ``httpx.AsyncClient`` for connection pooling, errors
logged then propagated to the caller (the admin route decides whether to
swallow or surface them).

Only the outbound *call-initiation* call lives here. When Plivo connects the
call it fetches the ``answer_url`` we pass — that XML is served by
``channels/voice/webhook.py``.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from apps.api.config import settings

logger = structlog.get_logger(__name__)

# Shared connection pool, reused across calls to keep TLS handshakes off the
# hot path. Plivo's API is plain REST + HTTP Basic auth.
_http: httpx.AsyncClient = httpx.AsyncClient(timeout=10.0)

_PLIVO_BASE = "https://api.plivo.com/v1"


def is_configured() -> bool:
    """True only when every credential needed to place a real call is set.

    Without all three the admin route falls back to the local-dev stub — same
    convention as ``outbound_whatsapp`` skipping the real send when
    ``meta_access_token`` is unset.
    """
    return bool(
        settings.plivo_auth_id
        and settings.plivo_auth_token
        and settings.plivo_phone_number
    )


async def initiate_call(to_e164: str, answer_url: str) -> dict[str, Any]:
    """Place an outbound call via ``POST /Account/{id}/Call/``.

    Plivo dials ``to_e164`` from the configured Plivo number; when the callee
    answers, Plivo fetches ``answer_url`` for the Plivo XML describing what to
    do. Returns the raw JSON response (contains ``request_uuid``). Raises
    ``httpx.HTTPStatusError`` on a non-2xx response.
    """
    url = f"{_PLIVO_BASE}/Account/{settings.plivo_auth_id}/Call/"
    payload = {
        "from": settings.plivo_phone_number,
        "to": to_e164,
        "answer_url": answer_url,
        "answer_method": "POST",
    }
    try:
        r = await _http.post(
            url,
            json=payload,
            auth=(settings.plivo_auth_id or "", settings.plivo_auth_token or ""),
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "plivo_initiate_call_failed",
            to=to_e164,
            error=str(exc),
            status=getattr(getattr(exc, "response", None), "status_code", None),
        )
        raise

    data: dict[str, Any] = r.json()
    logger.info(
        "plivo_initiate_call_ok",
        to=to_e164,
        request_uuid=data.get("request_uuid"),
    )
    return data
