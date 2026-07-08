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


_INBOUND_APP_NAME = "veerox-voice-app"


async def register_inbound_answer_url() -> None:
    """Point the Plivo number's inbound Answer URL at PUBLIC_BASE_URL/voice/answer.

    Runs on every app startup so the number always follows wherever this
    backend is currently deployed, instead of needing a manual Plivo
    console/API step after every redeploy to a new host. Idempotent: reuses
    the existing ``veerox-voice-app`` Application if present, else creates it.
    """
    if not is_configured():
        return

    answer_url = f"{settings.public_base_url.rstrip('/')}/voice/answer"
    number = (settings.plivo_phone_number or "").lstrip("+")
    base = f"{_PLIVO_BASE}/Account/{settings.plivo_auth_id}"
    auth = (settings.plivo_auth_id or "", settings.plivo_auth_token or "")

    try:
        r = await _http.get(f"{base}/Application/", params={"limit": 20}, auth=auth)
        r.raise_for_status()
        existing = next(
            (a for a in r.json().get("objects", []) if a.get("app_name") == _INBOUND_APP_NAME),
            None,
        )

        if existing:
            app_id = existing["app_id"]
            r = await _http.post(
                f"{base}/Application/{app_id}/",
                json={"answer_url": answer_url, "answer_method": "POST"},
                auth=auth,
            )
            r.raise_for_status()
        else:
            r = await _http.post(
                f"{base}/Application/",
                json={
                    "app_name": _INBOUND_APP_NAME,
                    "answer_url": answer_url,
                    "answer_method": "POST",
                },
                auth=auth,
            )
            r.raise_for_status()
            app_id = r.json()["app_id"]

        r = await _http.post(f"{base}/Number/{number}/", json={"app_id": app_id}, auth=auth)
        r.raise_for_status()
        logger.info("plivo_inbound_answer_url_registered", answer_url=answer_url, app_id=app_id)
    except httpx.HTTPError as exc:
        logger.warning("plivo_inbound_registration_failed", error=str(exc))
