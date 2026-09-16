"""Meta Cloud API webhook — verification handshake + inbound receipt.

The POST handler does the absolute minimum on the request thread (signature
verification + parse + enqueue) and hands the message off to the adapter
via FastAPI ``BackgroundTasks``. Meta retries any webhook that doesn't ACK
within ~15 seconds — see ``longrunning/operations/pitfalls.md``.

Each org configures its OWN Meta WhatsApp App (see
``core/org_credentials.py`` — no platform-wide fallback for client orgs; the
platform's own owner org is the one exception, falling back to
``settings.meta_verify_token``/``settings.meta_app_secret`` when it hasn't
saved its own), so there is no longer a single platform-wide secret to check
every webhook against. Meta webhooks are configured per-App: every org's
owner registers OUR shared callback URL (this endpoint) in THEIR OWN Meta App
dashboard, using their own verify token — usually automatic now (see
``channels/whatsapp/webhook_registration.py``), only manual if that
registration call fails. That means:

- The GET handshake (``verify_webhook``) must accept a token that matches
  ANY org's configured ``meta_verify_token`` — it has no other signal to
  say which org is doing the handshake.
- The POST signature check (``_verify_signature``) can only be done AFTER
  peeking at the body to learn which org's WABA number
  (`metadata.phone_number_id`) this message claims to be for, then
  verifying against THAT org's own ``meta_app_secret`` — so org A's secret
  can never be used to forge a webhook claiming to be for org B's number.
"""

from __future__ import annotations

import hmac
import json
from hashlib import sha256
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import or_, select

from apps.api.channels.whatsapp.adapter import process_inbound
from apps.api.config import settings
from apps.api.core.org_credentials import resolve_meta_credentials
from apps.api.db.models.org import Org
from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.deps import DbDep

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["whatsapp"])

_DEFAULT_ORG_ID = UUID(settings.default_org_id)


# Meta sends the signature with the ``sha256=`` prefix. Comparing the prefix
# along with the digest keeps the hmac.compare_digest call constant-time and
# format-aware.
_SIGNATURE_HEADER = "X-Hub-Signature-256"
_SIGNATURE_PREFIX = "sha256="


def _extract_phone_number_id(payload: dict[str, Any]) -> str | None:
    """The WABA number (Meta's `metadata.phone_number_id`) this webhook
    claims to be for — present on every inbound envelope, used to figure
    out which org's `meta_app_secret` to verify the signature against."""
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            phone_number_id = ((change.get("value") or {}).get("metadata") or {}).get(
                "phone_number_id"
            )
            if phone_number_id:
                return str(phone_number_id)
    return None


async def _resolve_org_by_phone_number_id(db: DbDep, phone_number_id: str) -> Org | None:
    """The org that owns this WABA number, via its OrgPhoneNumber row(s)."""
    org_id = (
        await db.execute(
            select(OrgPhoneNumber.org_id).where(
                OrgPhoneNumber.provider == "whatsapp",
                OrgPhoneNumber.phone_number == phone_number_id,
            )
        )
    ).scalars().first()
    if org_id is None:
        return None
    return await db.get(Org, org_id)


async def _verify_signature(db: DbDep, body: bytes, header_value: str | None) -> bool:
    """Constant-time HMAC-SHA256 verification of the raw request body,
    against the app_secret of whichever org owns the phone_number_id inside
    the (already-parsed) body — never a platform-wide secret.

    Rejects (returns False) when the header is missing/malformed, the body
    doesn't name a resolvable phone_number_id, that org has no app_secret
    configured, or the digest doesn't match — same "reject rather than
    silently accept" behavior as before, just re-scoped to per-org secrets.
    """
    if not header_value or not header_value.startswith(_SIGNATURE_PREFIX):
        return False

    try:
        payload: dict[str, Any] = json.loads(body)
    except ValueError:
        return False

    phone_number_id = _extract_phone_number_id(payload)
    if not phone_number_id:
        logger.warning("whatsapp_webhook_no_phone_number_id")
        return False

    org = await _resolve_org_by_phone_number_id(db, phone_number_id)
    creds = resolve_meta_credentials(org)
    if creds is None:
        logger.warning("whatsapp_webhook_org_not_resolved", phone_number_id=phone_number_id)
        return False

    expected = hmac.new(creds.app_secret.encode("utf-8"), body, sha256).hexdigest()
    expected_full = f"{_SIGNATURE_PREFIX}{expected}"
    return hmac.compare_digest(expected_full, header_value)


@router.get("/webhook/whatsapp")
async def verify_webhook(
    db: DbDep,
    # FastAPI translates dots in query-param names to underscores: Meta sends
    # ``hub.mode`` / ``hub.verify_token`` / ``hub.challenge`` on the wire, but
    # we receive them here as ``hub_mode`` / ``hub_verify_token`` / ``hub_challenge``.
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
) -> PlainTextResponse:
    """Meta verification handshake. Must complete in <10s.

    Accepts when the presented token matches ANY org's own
    ``meta_verify_token`` — each org configures our shared callback URL in
    its own Meta App dashboard with its own token, so there's no single
    platform-wide token to check against any more.
    """
    if hub_mode == "subscribe" and hub_verify_token and hub_challenge is not None:
        # Candidates: every org with its own verify token saved, plus the
        # platform's own owner org even if it hasn't saved one — it falls
        # back to settings.meta_verify_token (see core/org_credentials.py).
        orgs = (
            await db.execute(
                select(Org).where(
                    or_(
                        Org.meta_verify_token_encrypted.is_not(None),
                        Org.id == _DEFAULT_ORG_ID,
                    )
                )
            )
        ).scalars().all()
        for org in orgs:
            creds = resolve_meta_credentials(org)
            if creds and creds.verify_token and hmac.compare_digest(hub_verify_token, creds.verify_token):
                logger.info("whatsapp_webhook_verified", org_id=str(org.id))
                return PlainTextResponse(hub_challenge)

    logger.warning(
        "whatsapp_webhook_verification_failed",
        mode=hub_mode,
        token_present=bool(hub_verify_token),
    )
    raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/webhook/whatsapp")
async def receive_webhook(
    request: Request,
    background: BackgroundTasks,
    db: DbDep,
) -> dict[str, str]:
    """Inbound message receipt.

    Order is load-bearing: raw body first (for HMAC + to peek at
    phone_number_id), then verify against that org's own app_secret, then
    enqueue, then 200. Nothing unverified reaches ``process_inbound``. Do
    NOT await the LLM here — Meta retries on >15s and the user gets
    duplicate replies.
    """
    body_bytes = await request.body()
    signature = request.headers.get(_SIGNATURE_HEADER) or request.headers.get(
        _SIGNATURE_HEADER.lower()
    )

    if not await _verify_signature(db, body_bytes, signature):
        logger.warning("whatsapp_webhook_bad_signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload: dict[str, Any] = await request.json()
    except ValueError:
        # Meta sometimes sends an empty body for some operations — log and ack.
        logger.warning("whatsapp_webhook_invalid_json")
        return {"status": "ok"}

    background.add_task(process_inbound, payload)
    logger.info("whatsapp_webhook_received", object_type=payload.get("object"))
    return {"status": "ok"}
