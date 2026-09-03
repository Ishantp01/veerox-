"""Try Plivo first, then Twilio, for placing an outbound call.

``channels/voice/webhook.py`` and ``channels/voice/realtime_bridge.py`` are
provider-aware, so a call that fails over to Twilio reaches the same AI
voice bridge a Plivo call does — not just a ring with no answer script.

With no Twilio credentials set, this is a no-op passthrough to Plivo alone,
same as before this module existed — failing over needs somewhere to fail
over TO.

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

logger = structlog.get_logger(__name__)


def is_configured() -> bool:
    """True if at least one provider can place a call."""
    return plivo_client.is_configured() or twilio_client.is_configured()


async def initiate_call(
    to_e164: str,
    answer_url: str,
    hangup_url: str | None = None,
    plivo_from_number: str | None = None,
    twilio_from_number: str | None = None,
    preferred_provider: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Place an outbound call, preferring Plivo and falling back to Twilio.

    ``plivo_from_number`` / ``twilio_from_number``, when given, override each
    provider's configured caller ID — used to dial from an org's own
    dedicated number (see ``channels/voice/org_numbers.py::get_default_numbers``
    and ``db/models/org_phone_number.py``) instead of the platform default. An org can
    have a dedicated number on BOTH providers at once, so when
    ``twilio_from_number`` is set and ``plivo_from_number`` isn't, Twilio is
    tried FIRST — dialing from Plivo with no matching number for this org
    would just be the platform default caller ID, not this org's own line.
    ``preferred_provider`` (``"plivo"`` or ``"twilio"``), when given,
    overrides that inference outright — e.g. the dashboard's calling page
    lets an org with dedicated numbers on both providers explicitly choose
    which one to dial from. Either way the other provider is still tried as
    a fallback (using ITS platform default, or its own dedicated number when
    the org has one) so a provider-wide outage doesn't strand the org's
    calls entirely — this is a preference, not a hard restriction.

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
        client = plivo_client if name == "plivo" else twilio_client
        if not client.is_configured():
            continue
        try:
            result = await client.initiate_call(
                to_e164, answer_url, hangup_url=hangup_url, from_number=from_number
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
    raise RuntimeError("No voice provider (Plivo or Twilio) is configured.")


def is_sms_configured() -> bool:
    """True if at least one provider can send an SMS."""
    return plivo_client.is_configured() or twilio_client.is_configured()


async def send_sms(to_e164: str, text: str) -> tuple[dict[str, Any], str]:
    """Send an SMS, preferring Plivo and falling back to Twilio.

    Same shape/behavior as ``initiate_call`` above: returns
    ``(response_json, provider_name)``, raises the primary provider's error
    (not the fallback's) if both fail.
    """
    providers = [
        ("plivo", plivo_client.is_configured, plivo_client.send_sms),
        ("twilio", twilio_client.is_configured, twilio_client.send_sms),
    ]

    primary_error: httpx.HTTPError | None = None
    for name, configured, send in providers:
        if not configured():
            continue
        try:
            result = await send(to_e164, text)
            return result, name
        except httpx.HTTPError as exc:
            logger.warning(f"{name}_sms_failed_trying_fallback", to=to_e164, error=str(exc))
            if primary_error is None:
                primary_error = exc
            else:
                raise primary_error from None

    if primary_error is not None:
        raise primary_error
    raise RuntimeError("No SMS provider (Plivo or Twilio) is configured.")
