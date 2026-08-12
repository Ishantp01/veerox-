"""Outbound call client for the Twilio Voice API.

Backup provider for ``channels/voice/failover.py``. ``channels/voice/webhook.py``
and ``channels/voice/realtime_bridge.py`` are provider-aware so a Twilio call
reaches the same AI voice bridge Plivo calls do — see those modules for the
TwiML / Twilio Media Streams side of this.

CAVEAT: written against Twilio's documented REST + Media Streams API with no
real Twilio account to place a test call against (none was configured when
this was built). Verify end-to-end with a real Twilio number before relying
on it in production — message/param shapes are a common source of drift
between docs and actual behavior.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from apps.api.config import settings

logger = structlog.get_logger(__name__)

_http: httpx.AsyncClient = httpx.AsyncClient(timeout=10.0)

_TWILIO_BASE = "https://api.twilio.com/2010-04-01"


def is_configured() -> bool:
    return bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_phone_number
    )


async def initiate_call(
    to_e164: str,
    answer_url: str,
    hangup_url: str | None = None,
    from_number: str | None = None,
) -> dict[str, Any]:
    """Place an outbound call via ``POST /Accounts/{sid}/Calls.json``.

    Mirrors ``plivo_client.initiate_call``'s signature (including
    ``hangup_url`` and ``from_number``) so ``channels/voice/failover.py`` can
    call either provider interchangeably. ``from_number`` overrides the
    platform-wide ``settings.twilio_phone_number`` — used to dial from an
    org's own dedicated Twilio number (see ``Org.twilio_phone_number``).
    Twilio has no separate "hangup webhook" concept like Plivo — the same
    effect is a ``StatusCallback`` fired on terminal call-status events, so
    ``hangup_url`` maps to that here. Raises ``httpx.HTTPStatusError`` on a
    non-2xx response.
    """
    url = f"{_TWILIO_BASE}/Accounts/{settings.twilio_account_sid}/Calls.json"
    data: dict[str, str] = {
        "To": to_e164,
        "From": from_number or settings.twilio_phone_number or "",
        "Url": answer_url,
        "Method": "POST",
    }
    if hangup_url:
        data["StatusCallback"] = hangup_url
        data["StatusCallbackMethod"] = "POST"
        # Twilio fires the callback once per event listed here; these four
        # cover the same "call is over" cases Plivo's hangup_url reports.
        data["StatusCallbackEvent"] = "completed"
    try:
        r = await _http.post(
            url,
            data=data,
            auth=(settings.twilio_account_sid or "", settings.twilio_auth_token or ""),
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "twilio_initiate_call_failed",
            to=to_e164,
            error=str(exc),
            status=getattr(getattr(exc, "response", None), "status_code", None),
        )
        raise

    result: dict[str, Any] = r.json()
    logger.info("twilio_initiate_call_ok", to=to_e164, call_sid=result.get("sid"))
    return result


async def owns_number(e164: str) -> bool:
    """True if ``e164`` is a number in this Twilio account — used by
    ``channels/voice/number_provider.py::detect_provider`` to figure out
    which provider an admin-entered calling number belongs to. Treats any
    request failure as "no".
    """
    if not (settings.twilio_account_sid and settings.twilio_auth_token) or not e164:
        return False
    url = f"{_TWILIO_BASE}/Accounts/{settings.twilio_account_sid}/IncomingPhoneNumbers.json"
    try:
        r = await _http.get(
            url,
            params={"PhoneNumber": e164},
            auth=(settings.twilio_account_sid or "", settings.twilio_auth_token or ""),
        )
        r.raise_for_status()
        return bool(r.json().get("incoming_phone_numbers"))
    except httpx.HTTPError as exc:
        logger.warning("twilio_owns_number_check_failed", number=e164, error=str(exc))
        return False


async def start_recording(call_sid: str, callback_url: str) -> None:
    """Start server-side call recording via ``POST /Calls/{call_sid}/Recordings.json``.

    Mirrors ``plivo_client.start_recording``: best-effort, never raises,
    since a failed recording request shouldn't fail call answering. Twilio
    hosts the resulting audio itself and posts ``RecordingUrl`` /
    ``RecordingDuration`` (seconds, not ms — unlike Plivo's
    ``RecordingDurationMs``) to ``callback_url`` when ready.
    """
    if not is_configured():
        return

    url = f"{_TWILIO_BASE}/Accounts/{settings.twilio_account_sid}/Calls/{call_sid}/Recordings.json"
    try:
        r = await _http.post(
            url,
            data={
                "RecordingStatusCallback": callback_url,
                "RecordingStatusCallbackMethod": "POST",
                "RecordingStatusCallbackEvent": "completed",
            },
            auth=(settings.twilio_account_sid or "", settings.twilio_auth_token or ""),
        )
        r.raise_for_status()
        logger.info("twilio_recording_started", call_sid=call_sid)
    except httpx.HTTPError as exc:
        logger.warning("twilio_recording_start_failed", call_sid=call_sid, error=str(exc))


async def send_sms(to_e164: str, text: str) -> dict[str, Any]:
    """Send an SMS via ``POST /Accounts/{sid}/Messages.json``.

    Backup for ``plivo_client.send_sms`` (see ``channels/voice/failover.py``'s
    ``send_sms``) — same Twilio credentials as calling failover
    (``settings.twilio_*``). Raises ``httpx.HTTPStatusError`` on a non-2xx
    response.
    """
    url = f"{_TWILIO_BASE}/Accounts/{settings.twilio_account_sid}/Messages.json"
    try:
        r = await _http.post(
            url,
            data={
                "To": to_e164,
                "From": settings.twilio_phone_number or "",
                "Body": text,
            },
            auth=(settings.twilio_account_sid or "", settings.twilio_auth_token or ""),
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "twilio_send_sms_failed",
            to=to_e164,
            error=str(exc),
            status=getattr(getattr(exc, "response", None), "status_code", None),
        )
        raise

    result: dict[str, Any] = r.json()
    logger.info("twilio_send_sms_ok", to=to_e164, message_sid=result.get("sid"))
    return result


async def hangup_call(call_sid: str) -> None:
    """Force-terminate a live call via ``POST /Calls/{call_sid}.json`` with
    ``Status=completed``. Best-effort: never raises, mirroring
    ``plivo_client.hangup_call`` (used when a plan's usage limit is hit
    mid-call — see realtime_bridge.py's ``_watch_usage_limit``).
    """
    if not is_configured():
        return

    url = f"{_TWILIO_BASE}/Accounts/{settings.twilio_account_sid}/Calls/{call_sid}.json"
    try:
        r = await _http.post(
            url,
            data={"Status": "completed"},
            auth=(settings.twilio_account_sid or "", settings.twilio_auth_token or ""),
        )
        r.raise_for_status()
        logger.info("twilio_call_hungup", call_sid=call_sid)
    except httpx.HTTPError as exc:
        logger.warning("twilio_hangup_failed", call_sid=call_sid, error=str(exc))
