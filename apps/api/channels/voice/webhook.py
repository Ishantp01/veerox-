"""Voice answer webhook — bridges the call audio to the realtime stream.

Plivo or Twilio (see channels/voice/failover.py) fetches this endpoint when
a call is answered and executes whatever markup we return. Both get a
**bidirectional** mu-law (8 kHz) audio stream to ``/voice/stream`` — the
realtime bridge (``channels/voice/realtime_bridge.py``) then pipes audio
between the caller and the OpenAI Realtime API — but the markup dialect and
the outbound WebSocket message shapes differ per provider, so this endpoint
detects which one is asking (Twilio's POST body carries ``CallSid`` +
``AccountSid``; Plivo's carries ``CallUUID``) and responds accordingly.

The caller's number + call ID + provider are passed through on the WebSocket
URL's query string so the bridge can resolve the caller to a User row, tag
the conversation, and speak the right dialect back.

Twilio side is unverified against a real account — see
channels/voice/twilio_client.py's module docstring.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode

import structlog
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import Response

from sqlalchemy import select

from apps.api.channels.voice import adapter as voice_adapter
from apps.api.channels.voice import plivo_client as voice_plivo
from apps.api.channels.voice import twilio_client as voice_twilio
from apps.api.config import settings
from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal
from apps.api.workers import campaign_dialer

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


def _xml_escape(value: str) -> str:
    """Escape the five XML predefined entities for safe attribute/text use."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _ws_stream_url(
    caller: str,
    call_uuid: str,
    campaign_target_id: str | None = None,
    org_id: str | None = None,
    provider: str = "plivo",
) -> str:
    """Build the ``wss://`` URL Plivo/Twilio streams to, carrying caller context.

    Scheme is derived from ``PUBLIC_BASE_URL`` (https -> wss, http -> ws). The
    bridge reads ``from`` / ``call_uuid`` / ``campaign_target_id`` / ``org_id`` /
    ``provider`` from the query string — ``provider`` is what tells it which
    outbound message dialect to speak back (see realtime_bridge.py).
    """
    base = settings.public_base_url.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://") :]
    else:
        ws_base = "wss://" + base
    params = {"from": caller, "call_uuid": call_uuid, "provider": provider}
    if campaign_target_id:
        params["campaign_target_id"] = campaign_target_id
    if org_id:
        params["org_id"] = org_id
    qs = urlencode(params)
    return f"{ws_base}/voice/stream?{qs}"


def _normalize_phone(value: str) -> str:
    """Digits only, no `+` — the stored convention for `Org.plivo_phone_number`
    (see routers/admin.py's update_calling_number, which normalizes the same
    way on save) so lookups here are a plain equality check."""
    return re.sub(r"\D", "", value or "")


async def _resolve_org_by_number(to_number: str | None, is_twilio: bool) -> str | None:
    """The org whose dedicated number this inbound call was dialed on, if any.

    Checks ``Org.twilio_phone_number`` for calls routed via Twilio and
    ``Org.plivo_phone_number`` for Plivo — an org's dedicated number lives on
    whichever provider's account actually owns it (see
    ``channels/voice/number_provider.py::detect_provider``), never both.

    Only meaningful for calls Plivo/Twilio route to us unprompted (a real
    inbound call) — an admin/campaign-placed outbound call already carries
    its own ``org_id`` on the answer_url and takes priority over this (see
    ``answer`` below), same as ``channels/whatsapp/adapter.py``'s WABA-number
    lookup for the equivalent WhatsApp gap.
    """
    if not to_number:
        return None
    normalized = _normalize_phone(to_number)
    if not normalized:
        return None
    column = Org.twilio_phone_number if is_twilio else Org.plivo_phone_number
    async with AsyncSessionLocal() as db:
        org_id = (await db.execute(select(Org.id).where(column == normalized))).scalar_one_or_none()
    return str(org_id) if org_id else None


async def _call_params(request: Request) -> dict[str, str]:
    """Read Plivo's call params from POST form-body or GET query string.

    Parsed manually (urllib) to avoid a hard dependency on ``python-multipart``
    just for a flat urlencoded body.
    """
    if request.method == "POST":
        raw = (await request.body()).decode("utf-8", "ignore")
        return {k: v[0] for k, v in parse_qs(raw).items()}
    return dict(request.query_params)


@router.api_route("/answer", methods=["GET", "POST"])
async def answer(request: Request, background: BackgroundTasks) -> Response:
    """Return the markup executed when a call is answered — Plivo XML or
    TwiML depending on which provider is asking.

    Twilio's POST body carries ``CallSid`` + ``AccountSid`` and never
    ``CallUUID``; Plivo's carries ``CallUUID``. That distinction is reliable
    enough to branch on without needing the provider explicit anywhere else
    in the request.
    """
    params = await _call_params(request)
    caller = params.get("From") or params.get("from") or "unknown"
    is_twilio = "CallSid" in params and "CallUUID" not in params
    call_uuid = params.get("CallUUID") or params.get("call_uuid") or params.get("CallSid") or ""
    # Set by the campaign dialer on the answer_url it gives the provider (the
    # provider's own POST body never carries it) — always present in the URL
    # query string regardless of request method.
    campaign_target_id = request.query_params.get("campaign_target_id")
    # Set by routers/admin.py's outbound_call on the answer_url it gives the
    # provider for a dashboard-placed single call (campaign calls resolve
    # their org from the campaign itself instead — see
    # realtime_bridge._resolve_org_id).
    org_id = request.query_params.get("org_id")
    if not org_id and not campaign_target_id:
        # A real inbound call — nothing in the URL says which org, so look up
        # the dialed number's owner instead. Falls back to the platform
        # default further down the chain (realtime_bridge._resolve_org_id)
        # if the number isn't provisioned to any org yet.
        to_number = params.get("To") or params.get("to")
        org_id = await _resolve_org_by_number(to_number, is_twilio)

    provider = "twilio" if is_twilio else "plivo"
    ws_url = _ws_stream_url(caller, call_uuid, campaign_target_id, org_id, provider=provider)

    if is_twilio:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f'<Connect><Stream url="{_xml_escape(ws_url)}" /></Connect>'
            "</Response>"
        )
        logger.info("twilio_answer_served", caller=caller, call_uuid=call_uuid)
    else:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            '<Stream bidirectional="true" keepCallAlive="true" '
            'contentType="audio/x-mulaw;rate=8000" '
            f'streamTimeout="3600">{_xml_escape(ws_url)}</Stream>'
            "</Response>"
        )
        logger.info("plivo_answer_served", caller=caller, call_uuid=call_uuid)

    # Start recording via the provider's Call API — independent of the
    # stream markup above, so it doesn't interfere with the realtime audio
    # bridge. Deferred to a background task so a slow/failed provider
    # request never delays the answer response it's waiting on.
    if call_uuid:
        callback_url = f"{settings.public_base_url.rstrip('/')}/voice/recording-callback"
        if is_twilio and voice_twilio.is_configured():
            background.add_task(voice_twilio.start_recording, call_uuid, callback_url)
        elif not is_twilio and voice_plivo.is_configured():
            background.add_task(voice_plivo.start_recording, call_uuid, callback_url)

    return Response(content=xml, media_type="application/xml")


@router.api_route("/campaign-hangup", methods=["GET", "POST"])
async def campaign_hangup(request: Request) -> Response:
    """Plivo posts call-status changes here — set as ``hangup_url`` on calls
    the campaign dialer places (apps/api/workers/campaign_dialer.py). Lets
    the dialer learn a call ended within seconds, covering no-answer/busy/
    failed/dropped calls where ``qualify_lead`` never fires, rather than
    waiting on the dialer's stale-call timeout.
    """
    params = await _call_params(request)
    campaign_target_id = request.query_params.get("campaign_target_id")
    call_status = params.get("CallStatus") or params.get("call_status")

    if campaign_target_id:
        await campaign_dialer.handle_call_ended(campaign_target_id)
        logger.info(
            "plivo_campaign_hangup",
            campaign_target_id=campaign_target_id,
            call_status=call_status,
        )
    else:
        logger.warning("plivo_campaign_hangup_missing_target_id", params=params)

    return Response(status_code=204)


@router.api_route("/recording-callback", methods=["GET", "POST"])
async def recording_callback(request: Request) -> Response:
    """Plivo/Twilio posts here when a recording started in ``answer``
    finishes processing — ``RecordingUrl`` points at the provider's own
    hosted copy of the audio (no storage of our own needed), matched back to
    its Conversation via the call ID.

    Duration units differ: Plivo's ``RecordingDurationMs`` is milliseconds,
    Twilio's ``RecordingDuration`` is whole seconds.
    """
    params = await _call_params(request)
    call_uuid = (
        params.get("CallUUID")
        or params.get("call_uuid")
        or params.get("CallSid")
        or ""
    )
    recording_url = params.get("RecordingUrl") or params.get("recording_url")
    duration_ms = params.get("RecordingDurationMs") or params.get("recording_duration_ms")
    duration_secs_raw = params.get("RecordingDuration")

    duration_secs = None
    if duration_ms:
        try:
            duration_secs = float(duration_ms) / 1000.0
        except ValueError:
            duration_secs = None
    elif duration_secs_raw:
        try:
            duration_secs = float(duration_secs_raw)
        except ValueError:
            duration_secs = None

    if call_uuid and recording_url:
        found = await voice_adapter.save_call_recording(call_uuid, recording_url, duration_secs)
        logger.info(
            "plivo_recording_callback",
            call_uuid=call_uuid,
            matched=found,
            duration_secs=duration_secs,
        )
    else:
        logger.warning("plivo_recording_callback_missing_fields", params=params)

    return Response(status_code=204)
