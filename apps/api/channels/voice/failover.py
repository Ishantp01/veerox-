"""Try Plivo first, then Twilio, for placing an outbound call.

``channels/voice/webhook.py`` and ``channels/voice/realtime_bridge.py`` are
provider-aware, so a call that fails over to Twilio reaches the same AI
voice bridge a Plivo call does — not just a ring with no answer script.

Every function here takes this org's own resolved ``PlivoCredentials`` /
``TwilioCredentials`` (see ``core/org_credentials.py``, resolved from the
``Org`` row by the caller) — there is no platform-wide fallback. A provider
is only attempted when BOTH its credentials are configured AND a dedicated
number for it was passed in; an org missing either for a provider simply
skips that provider, same as "not configured" used to work.

CAVEAT: the Twilio side of this (answer webhook TwiML, Twilio Media Streams
message shapes, recording/hangup) was written against Twilio's documented
API with no real Twilio account to place a test call against. Treat it as
unverified until exercised against a live Twilio number.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from apps.api.channels.voice import plivo_client, twilio_client
from apps.api.core.org_credentials import PlivoCredentials, TwilioCredentials

logger = structlog.get_logger(__name__)


def is_configured(plivo_creds: PlivoCredentials | None, twilio_creds: TwilioCredentials | None) -> bool:
    """True if at least one provider can place a call."""
    return plivo_client.is_configured(plivo_creds) or twilio_client.is_configured(twilio_creds)


async def initiate_call(
    plivo_creds: PlivoCredentials | None,
    twilio_creds: TwilioCredentials | None,
    to_e164: str,
    answer_url: str,
    hangup_url: str | None = None,
    plivo_from_number: str | None = None,
    twilio_from_number: str | None = None,
    preferred_provider: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Place an outbound call, preferring Plivo and falling back to Twilio.

    ``plivo_from_number`` / ``twilio_from_number`` must be one of this org's
    own dedicated numbers on that provider (see
    ``channels/voice/org_numbers.py::get_rotating_numbers`` and
    ``db/models/org_phone_number.py``) — a provider with no matching number
    is skipped outright, same as having no credentials for it, since there's
    no platform-wide default number/account to fall back to. An org can have
    a dedicated number on BOTH providers at once, so when
    ``twilio_from_number`` is set and ``plivo_from_number`` isn't, Twilio is
    tried FIRST.
    ``preferred_provider`` (``"plivo"`` or ``"twilio"``), when given,
    overrides that inference outright — e.g. the dashboard's calling page
    lets an org with dedicated numbers on both providers explicitly choose
    which one to dial from. Either way the other provider is still tried as
    a fallback when it's configured, so a provider-wide outage doesn't
    strand the org's calls entirely — this is a preference, not a hard
    restriction.

    Returns ``(response_json, provider_name)`` so callers can log/attribute
    which provider actually placed the call. Raises the primary provider's
    error (not the fallback's) if both fail, since that's the one worth
    surfacing/alerting on.
    """
    if preferred_provider is not None:
        twilio_primary = preferred_provider == "twilio"
    else:
        twilio_primary = bool(twilio_from_number) and not plivo_from_number
    providers = (
        [("twilio", twilio_from_number), ("plivo", plivo_from_number)]
        if twilio_primary
        else [("plivo", plivo_from_number), ("twilio", twilio_from_number)]
    )

    primary_error: httpx.HTTPError | None = None
    for name, from_number in providers:
        if name == "plivo":
            if not plivo_client.is_configured(plivo_creds) or not from_number:
                continue
        else:
            if not twilio_client.is_configured(twilio_creds) or not from_number:
                continue
        client = plivo_client if name == "plivo" else twilio_client
        creds = plivo_creds if name == "plivo" else twilio_creds
        try:
            result = await client.initiate_call(
                creds, to_e164, answer_url, from_number, hangup_url=hangup_url
            )
            return result, name
        except httpx.HTTPError as exc:
            logger.warning(
                f"{name}_call_failed_trying_fallback",
                to=to_e164,
                error=str(exc),
            )
            if primary_error is None:
                primary_error = exc
            else:
                raise primary_error from None

    if primary_error is not None:
        raise primary_error
    raise RuntimeError("No voice provider (Plivo or Twilio) is configured for this org.")


def is_sms_configured(plivo_creds: PlivoCredentials | None, twilio_creds: TwilioCredentials | None) -> bool:
    """True if at least one provider can send an SMS."""
    return plivo_client.is_configured(plivo_creds) or twilio_client.is_configured(twilio_creds)


async def send_sms(
    plivo_creds: PlivoCredentials | None,
    twilio_creds: TwilioCredentials | None,
    to_e164: str,
    text: str,
    plivo_from_number: str | None = None,
    twilio_from_number: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Send an SMS, preferring Plivo and falling back to Twilio.

    Same shape/behavior as ``initiate_call`` above: returns
    ``(response_json, provider_name)``, raises the primary provider's error
    (not the fallback's) if both fail. Each ``*_from_number`` must be one of
    this org's own dedicated numbers on that provider — no platform default.
    """
    providers = [
        ("plivo", plivo_client, plivo_creds, plivo_from_number),
        ("twilio", twilio_client, twilio_creds, twilio_from_number),
    ]

    primary_error: httpx.HTTPError | None = None
    for name, client, creds, from_number in providers:
        if not client.is_configured(creds) or not from_number:
            continue
        try:
            result = await client.send_sms(creds, to_e164, text, from_number)
            return result, name
        except httpx.HTTPError as exc:
            logger.warning(f"{name}_sms_failed_trying_fallback", to=to_e164, error=str(exc))
            if primary_error is None:
                primary_error = exc
            else:
                raise primary_error from None

    if primary_error is not None:
        raise primary_error
    raise RuntimeError("No SMS provider (Plivo or Twilio) is configured for this org.")
