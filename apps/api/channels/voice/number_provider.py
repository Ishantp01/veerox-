"""Figure out which voice provider (Plivo or Twilio) owns a phone number.

An admin only enters one "calling number" per org — routers/auth.py's
provision_org and routers/admin.py's update_org_numbers use ``detect_provider``
to work out whether it belongs in ``Org.plivo_phone_number`` or
``Org.twilio_phone_number``, so channels/voice/failover.py can later dial
from (and fail over around) the correct provider for that org.
"""

from __future__ import annotations

import re

from apps.api.channels.voice import plivo_client, twilio_client


def _to_e164(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return f"+{digits}"


async def detect_provider(number: str) -> str | None:
    """Returns ``"plivo"``, ``"twilio"``, or ``None``.

    ``None`` means neither configured provider account owns this number (or
    neither provider has credentials set, e.g. local dev — callers should
    treat that case as "can't verify" rather than "invalid").
    """
    digits = re.sub(r"\D", "", number)
    if not digits:
        return None

    if plivo_client.is_configured() and await plivo_client.owns_number(digits):
        return "plivo"
    if twilio_client.is_configured() and await twilio_client.owns_number(_to_e164(number)):
        return "twilio"
    return None


async def resolve_calling_number(number: str) -> tuple[str, str]:
    """Normalize + classify an admin-entered calling number.

    Returns ``(digits, provider)`` where ``provider`` is ``"plivo"`` or
    ``"twilio"``. Defaults to ``"plivo"`` when neither account confirms
    ownership — either because neither provider has credentials configured
    (nothing to check against, e.g. local dev) or the number genuinely isn't
    in either account yet (kept permissive rather than rejecting outright,
    since the number may simply not be provisioned yet).
    """
    digits = re.sub(r"\D", "", number)
    provider = await detect_provider(number)
    return digits, provider or "plivo"
