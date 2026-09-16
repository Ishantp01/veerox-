"""Best-effort registration of an org's Meta webhook, via the Graph API
instead of the manual "paste this into the App dashboard" step.

Meta actually exposes both halves of webhook setup programmatically:

- ``POST /{app_id}/subscriptions`` sets the App's callback URL + verify
  token for the ``whatsapp_business_account`` object — this is the same
  thing the App Dashboard's "Webhooks" tab does, just callable with the
  App's own access token (``{app_id}|{app_secret}``, the standard Graph API
  "app access token" — no separate user/system-user token needed for this
  call).
- ``POST /{waba_id}/subscribed_apps`` subscribes that App to receive
  webhook events for a specific WhatsApp Business Account — needs the org's
  own ``access_token`` (system user / permanent token) and its
  ``business_account_id``.

Both are called together whenever an org has enough configured to make them
meaningful (app_id + app_secret + verify_token for the first; additionally
business_account_id + access_token for the second) — see
``register_org_webhook``. Swallows all exceptions: a Graph API hiccup here
must never block a settings save or org creation.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.org_credentials import resolve_meta_credentials
from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)

_http: httpx.AsyncClient = httpx.AsyncClient(timeout=10.0)
_GRAPH_BASE = "https://graph.facebook.com"


async def register_org_webhook(db: AsyncSession, org_id: UUID | str) -> None:
    """Point this org's Meta App at our shared webhook URL with its own
    verify token, then subscribe that App to the org's WABA. No-op (logs and
    returns) when the org's Meta credentials aren't complete enough for a
    given half of this — each half is independently best-effort."""
    org_record = await db.get(Org, org_id)
    creds = resolve_meta_credentials(org_record)
    if creds is None:
        return

    callback_url = f"{settings.public_base_url.rstrip('/')}/webhook/whatsapp"
    version = settings.meta_graph_api_version

    if creds.verify_token:
        try:
            r = await _http.post(
                f"{_GRAPH_BASE}/{version}/{creds.app_id}/subscriptions",
                data={
                    "object": "whatsapp_business_account",
                    "callback_url": callback_url,
                    "verify_token": creds.verify_token,
                    "fields": "messages",
                    "access_token": f"{creds.app_id}|{creds.app_secret}",
                },
            )
            r.raise_for_status()
            logger.info("meta_webhook_subscription_registered", org_id=str(org_id))
        except httpx.HTTPError as exc:
            logger.warning(
                "meta_webhook_subscription_failed",
                org_id=str(org_id),
                error=str(exc),
                status=getattr(getattr(exc, "response", None), "status_code", None),
                body=getattr(getattr(exc, "response", None), "text", None),
            )
    else:
        logger.info("meta_webhook_subscription_skipped_no_verify_token", org_id=str(org_id))

    if creds.business_account_id:
        try:
            r = await _http.post(
                f"{_GRAPH_BASE}/{version}/{creds.business_account_id}/subscribed_apps",
                params={"access_token": creds.access_token},
            )
            r.raise_for_status()
            logger.info("meta_waba_app_subscribed", org_id=str(org_id))
        except httpx.HTTPError as exc:
            logger.warning(
                "meta_waba_subscribe_failed",
                org_id=str(org_id),
                error=str(exc),
                status=getattr(getattr(exc, "response", None), "status_code", None),
                body=getattr(getattr(exc, "response", None), "text", None),
            )
    else:
        logger.info("meta_waba_subscribe_skipped_no_business_account_id", org_id=str(org_id))


def fire_and_forget_register_org_webhook(org_id: UUID | str) -> None:
    """Schedule ``register_org_webhook`` as a background task without
    blocking or letting its exceptions propagate to the caller — mirrors
    ``channels/voice/plivo_provisioning.py::fire_and_forget_register_org_plivo_numbers``.
    Opens its own DB session since the triggering request's session may
    already be closed by the time the Graph API calls finish.
    """

    async def _run() -> None:
        try:
            async with AsyncSessionLocal() as db:
                await register_org_webhook(db, org_id)
        except Exception:
            logger.warning("meta_webhook_registration_task_failed", org_id=str(org_id), exc_info=True)

    asyncio.create_task(_run())
