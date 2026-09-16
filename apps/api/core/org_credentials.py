"""Resolve an org's own Plivo/Twilio/Meta WhatsApp channel credentials.

Every client org must configure its own Plivo/Twilio account and Meta
WhatsApp App (set via PUT /admin/settings/{plivo,twilio,meta}-credentials,
see routers/admin.py) — there is NO fallback to the platform-wide
settings.plivo_*/twilio_*/meta_* env vars for them. An org with nothing (or
incomplete) configured for a provider simply can't call/SMS/send-WhatsApp
through that provider: every resolver returns None, and callers treat that
the same as "not configured" (channels/voice/plivo_client.py::is_configured
and friends).

ONE exception: the platform's own owner org (``settings.default_org_id`` —
same org ``is_platform_org`` flags elsewhere, e.g. schemas/auth.py's
SessionOut) falls back to the platform-wide env credentials when it hasn't
saved its own in the DB. This is Veerox's own operating account, not a
client — the env vars were always *its* credentials in the first place, so
this is a safety net for that one org rather than a platform-wide default.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.api.config import settings
from apps.api.core.crypto import decrypt_secret
from apps.api.db.models.org import Org


@dataclass(frozen=True)
class PlivoCredentials:
    auth_id: str
    auth_token: str


@dataclass(frozen=True)
class TwilioCredentials:
    account_sid: str
    auth_token: str


@dataclass(frozen=True)
class MetaCredentials:
    app_id: str
    app_secret: str
    access_token: str
    business_account_id: str | None
    verify_token: str | None


def _is_owner_org(org: Org | None) -> bool:
    """True for the platform's own operating org — see module docstring."""
    return org is not None and str(org.id) == settings.default_org_id


def resolve_plivo_credentials(org: Org | None) -> PlivoCredentials | None:
    if org is not None and org.plivo_auth_id and org.plivo_auth_token_encrypted:
        auth_token = decrypt_secret(org.plivo_auth_token_encrypted)
        if auth_token:
            return PlivoCredentials(auth_id=org.plivo_auth_id, auth_token=auth_token)
    if _is_owner_org(org) and settings.plivo_auth_id and settings.plivo_auth_token:
        return PlivoCredentials(auth_id=settings.plivo_auth_id, auth_token=settings.plivo_auth_token)
    return None


def resolve_twilio_credentials(org: Org | None) -> TwilioCredentials | None:
    if org is not None and org.twilio_account_sid and org.twilio_auth_token_encrypted:
        auth_token = decrypt_secret(org.twilio_auth_token_encrypted)
        if auth_token:
            return TwilioCredentials(account_sid=org.twilio_account_sid, auth_token=auth_token)
    if _is_owner_org(org) and settings.twilio_account_sid and settings.twilio_auth_token:
        return TwilioCredentials(
            account_sid=settings.twilio_account_sid, auth_token=settings.twilio_auth_token
        )
    return None


def resolve_meta_credentials(org: Org | None) -> MetaCredentials | None:
    """Requires app_id + app_secret + access_token; business_account_id and
    verify_token are looked up separately where needed (template management,
    webhook handshake) so a partially-configured org can still receive/send
    once those three are in place."""
    if org is not None and org.meta_app_id and org.meta_app_secret_encrypted and org.meta_access_token_encrypted:
        app_secret = decrypt_secret(org.meta_app_secret_encrypted)
        access_token = decrypt_secret(org.meta_access_token_encrypted)
        if app_secret and access_token:
            verify_token = (
                decrypt_secret(org.meta_verify_token_encrypted)
                if org.meta_verify_token_encrypted
                else None
            )
            return MetaCredentials(
                app_id=org.meta_app_id,
                app_secret=app_secret,
                access_token=access_token,
                business_account_id=org.meta_whatsapp_business_account_id,
                verify_token=verify_token,
            )
    if _is_owner_org(org) and settings.meta_app_id and settings.meta_app_secret and settings.meta_access_token:
        return MetaCredentials(
            app_id=settings.meta_app_id,
            app_secret=settings.meta_app_secret,
            access_token=settings.meta_access_token,
            business_account_id=settings.meta_whatsapp_business_account_id,
            verify_token=settings.meta_verify_token,
        )
    return None
