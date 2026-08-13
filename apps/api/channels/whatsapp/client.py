"""Outbound + media client for the Meta WhatsApp Cloud API.

Three async functions plus a shared, module-level ``httpx.AsyncClient`` for
connection pooling. Transient HTTP errors are logged with ``structlog`` and
propagated to the caller — the adapter / admin route decides whether to
swallow or escalate.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import structlog

from apps.api.config import settings

logger = structlog.get_logger(__name__)


# Shared connection pool. Reused across all calls to keep TLS handshakes off
# the hot path for back-to-back outbound messages.
_http: httpx.AsyncClient = httpx.AsyncClient(timeout=10.0)


_GRAPH_BASE = "https://graph.facebook.com"


def _graph_url(path: str, phone_number_id: str | None = None) -> str:
    """Build a Graph API URL pinned to the configured version + phone-number id.

    ``path`` is anything that follows the phone-number id (e.g. ``"/messages"``
    or ``""``). Pass ``path=""`` for endpoints that *are* the phone-number id
    itself. ``phone_number_id`` overrides the platform-default
    ``settings.meta_phone_number_id`` — used to send from an org's own
    dedicated WhatsApp number (see ``Org.whatsapp_phone_number_id``).
    """
    return (
        f"{_GRAPH_BASE}/{settings.meta_graph_api_version}"
        f"/{phone_number_id or settings.meta_phone_number_id}{path}"
    )


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.meta_access_token}"}


def _meta_error_detail(exc: httpx.HTTPError) -> dict[str, Any] | None:
    """Pull Meta's structured error body (code, message, error_data) off a
    failed response, when there is one.

    ``str(exc)`` alone (what every send function logged before this) only
    gives the HTTP status line, e.g. "400 Bad Request" - it discards the
    JSON body where Meta actually explains *why* (invalid/unapproved
    template, expired token, disallowed re-engagement, mismatched
    component structure, ...). Without this, every failed send showed up in
    logs as an opaque "Failed" with no way to tell those cases apart.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else None


# Meta error codes we can explain better than Meta's own wording does, in
# one short, plain-language sentence each — no jargon, no error codes.
_ERROR_CODE_HINTS: dict[int, str] = {
    131047: "This contact hasn't messaged you recently — use a Template instead of free text.",
    131058: "The hello_world sample template can't be sent to real contacts — pick a different template.",
    132001: "This template isn't set up in WhatsApp Manager yet — create and approve it there first.",
    131026: "This number can't receive WhatsApp messages.",
    133010: "This WhatsApp number isn't set up yet.",
}


def friendly_error_message(meta_error: dict[str, Any] | None) -> str:
    """Turn a Meta Graph API error into one short, plain-language sentence.

    Prefers a known, human-worded explanation for common error codes; falls
    back to Meta's own message (its "(#code) " prefix stripped) when the
    code isn't one we recognise.
    """
    if not meta_error:
        return "Message couldn't be sent."

    code = meta_error.get("code")
    hint = _ERROR_CODE_HINTS.get(code) if isinstance(code, int) else None
    if hint:
        return hint

    return (
        re.sub(r"^\(#\d+\)\s*", "", meta_error.get("message", "")).strip()
        or "Message couldn't be sent."
    )


async def send_text(to_e164: str, body: str, phone_number_id: str | None = None) -> dict[str, Any]:
    """Send a plain-text WhatsApp message via the Graph API.

    ``phone_number_id``, when given, sends from an org's own dedicated
    WhatsApp number instead of the platform default (see
    ``Org.whatsapp_phone_number_id``).

    Returns the raw JSON response (which contains the outbound message id).
    Raises ``httpx.HTTPStatusError`` on a non-2xx response.
    """
    url = _graph_url("/messages", phone_number_id)
    payload = {
        "messaging_product": "whatsapp",
        "to": to_e164,
        "type": "text",
        "text": {"body": body},
    }
    try:
        r = await _http.post(url, json=payload, headers=_auth_headers())
        r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "whatsapp_send_text_failed",
            to=to_e164,
            error=str(exc),
            status=getattr(getattr(exc, "response", None), "status_code", None),
            meta_error=_meta_error_detail(exc),
        )
        raise

    data: dict[str, Any] = r.json()
    logger.info(
        "whatsapp_send_text_ok",
        to=to_e164,
        wa_message_id=_extract_outbound_id(data),
    )
    return data


async def send_template(
    to_e164: str,
    template_name: str,
    language_code: str = "en_US",
    body_params: list[str] | None = None,
    phone_number_id: str | None = None,
) -> dict[str, Any]:
    """Send a pre-approved WhatsApp **template** message via the Graph API.

    Templates are the only way to message a user *outside* the 24-hour
    customer-service window — free-form text there raises Meta error 131047
    ("re-engagement message"). The template must already be approved in
    WhatsApp Manager.

    ``body_params`` fills the ``{{1}}``, ``{{2}}`` ... placeholders in the
    template body, in order. Pass ``None`` for templates with no variables
    (e.g. the built-in ``hello_world``). ``phone_number_id`` overrides the
    platform default the same way it does for ``send_text``.

    Returns the raw JSON response (contains the outbound message id). Raises
    ``httpx.HTTPStatusError`` on a non-2xx response.
    """
    url = _graph_url("/messages", phone_number_id)
    template: dict[str, Any] = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if body_params:
        template["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body_params],
            }
        ]
    payload = {
        "messaging_product": "whatsapp",
        "to": to_e164,
        "type": "template",
        "template": template,
    }
    try:
        r = await _http.post(url, json=payload, headers=_auth_headers())
        r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "whatsapp_send_template_failed",
            to=to_e164,
            template=template_name,
            error=str(exc),
            status=getattr(getattr(exc, "response", None), "status_code", None),
            meta_error=_meta_error_detail(exc),
        )
        raise

    data: dict[str, Any] = r.json()
    logger.info(
        "whatsapp_send_template_ok",
        to=to_e164,
        template=template_name,
        wa_message_id=_extract_outbound_id(data),
    )
    return data


async def list_templates() -> list[dict[str, Any]]:
    """Fetch this WABA's message templates with their live Meta review status.

    Used to show real approval status (``PENDING`` / ``APPROVED`` /
    ``REJECTED``) next to the locally-saved template rows, which otherwise
    have no status field of their own — creating a row in our DB is just a
    local metadata cache, it never touches Meta (see ``routers/templates.py``).
    """
    url = (
        f"{_GRAPH_BASE}/{settings.meta_graph_api_version}"
        f"/{settings.meta_whatsapp_business_account_id}/message_templates"
    )
    params = {"fields": "name,language,status,category", "limit": 200}
    try:
        r = await _http.get(url, params=params, headers=_auth_headers())
        r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("whatsapp_list_templates_failed", error=str(exc))
        raise
    data: dict[str, Any] = r.json()
    return list(data.get("data", []))


async def download_media(media_id: str) -> bytes:
    """Download a media blob from Meta in two steps.

    1. ``GET /{media_id}`` returns JSON with a short-lived signed ``url``.
    2. ``GET <url>`` (with the same Bearer header) returns the raw bytes.

    Raises ``httpx.HTTPStatusError`` on a non-2xx response from either call.
    """
    # Step 1: resolve the media id to a download URL.
    meta_url = f"{_GRAPH_BASE}/{settings.meta_graph_api_version}/{media_id}"
    try:
        meta_r = await _http.get(meta_url, headers=_auth_headers())
        meta_r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "whatsapp_media_lookup_failed",
            media_id=media_id,
            error=str(exc),
            status=getattr(getattr(exc, "response", None), "status_code", None),
        )
        raise

    meta_payload = meta_r.json()
    download_url = meta_payload.get("url")
    if not download_url:
        logger.warning("whatsapp_media_lookup_no_url", media_id=media_id, payload=meta_payload)
        raise RuntimeError(f"Meta media lookup returned no url for {media_id}")

    # Step 2: fetch the actual bytes. The CDN URL still requires the bearer
    # token — failing to send it returns a 401 with a misleading body.
    try:
        bin_r = await _http.get(download_url, headers=_auth_headers())
        bin_r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "whatsapp_media_download_failed",
            media_id=media_id,
            error=str(exc),
            status=getattr(getattr(exc, "response", None), "status_code", None),
        )
        raise

    logger.info(
        "whatsapp_media_downloaded",
        media_id=media_id,
        mime=meta_payload.get("mime_type"),
        bytes=len(bin_r.content),
    )
    return bin_r.content


async def mark_read(message_id: str, typing: bool = False, phone_number_id: str | None = None) -> None:
    """POST a read receipt for an inbound message.

    With ``typing=True`` the receipt also shows a typing indicator in the
    user's chat (auto-dismissed by Meta after ~25s or when we send a reply).
    ``phone_number_id`` overrides the platform default the same way it does
    for ``send_text``.

    Best-effort: failures are logged but do NOT raise — losing a read
    receipt should not poison the surrounding reply pipeline.
    """
    url = _graph_url("/messages", phone_number_id)
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    if typing:
        payload["typing_indicator"] = {"type": "text"}
    try:
        r = await _http.post(url, json=payload, headers=_auth_headers())
        r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "whatsapp_mark_read_failed",
            message_id=message_id,
            error=str(exc),
            status=getattr(getattr(exc, "response", None), "status_code", None),
        )
        return

    logger.info("whatsapp_mark_read_ok", message_id=message_id)


def _extract_outbound_id(payload: dict[str, Any]) -> str | None:
    """Pull the WhatsApp message id out of a Graph API send-text response."""
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            wa_id = first.get("id")
            if isinstance(wa_id, str):
                return wa_id
    return None
