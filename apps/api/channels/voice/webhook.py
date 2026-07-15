"""Plivo Voice answer webhook — bridges the call audio to the realtime stream.

When Plivo answers a call it fetches this endpoint and executes the returned
Plivo XML. We return a ``<Stream>`` element that opens a **bidirectional**
mu-law (8 kHz) audio stream to ``/voice/stream`` — the realtime bridge
(``channels/voice/realtime_bridge.py``) then pipes audio between Plivo and the
OpenAI Realtime API.

The caller's number + Call UUID are passed through on the WebSocket URL's query
string so the bridge can resolve the caller to a User row and tag the
conversation.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode

import structlog
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import Response

from apps.api.channels.voice import adapter as voice_adapter
from apps.api.channels.voice import plivo_client as voice_plivo
from apps.api.config import settings
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


def _ws_stream_url(caller: str, call_uuid: str, campaign_target_id: str | None = None) -> str:
    """Build the ``wss://`` URL Plivo streams to, carrying caller context.

    Scheme is derived from ``PUBLIC_BASE_URL`` (https -> wss, http -> ws). The
    bridge reads ``from`` / ``call_uuid`` / ``campaign_target_id`` from the
    query string.
    """
    base = settings.public_base_url.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://") :]
    else:
        ws_base = "wss://" + base
    params = {"from": caller, "call_uuid": call_uuid}
    if campaign_target_id:
        params["campaign_target_id"] = campaign_target_id
    qs = urlencode(params)
    return f"{ws_base}/voice/stream?{qs}"


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
    """Return the Plivo XML executed when a call is answered."""
    params = await _call_params(request)
    caller = params.get("From") or params.get("from") or "unknown"
    call_uuid = params.get("CallUUID") or params.get("call_uuid") or ""
    # Set by the campaign dialer on the answer_url it gives Plivo (Plivo's own
    # POST body never carries it) — always present in the URL query string
    # regardless of request method.
    campaign_target_id = request.query_params.get("campaign_target_id")

    ws_url = _ws_stream_url(caller, call_uuid, campaign_target_id)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Stream bidirectional="true" keepCallAlive="true" '
        'contentType="audio/x-mulaw;rate=8000" '
        f'streamTimeout="3600">{_xml_escape(ws_url)}</Stream>'
        "</Response>"
    )
    logger.info("plivo_answer_served", caller=caller, call_uuid=call_uuid)

    # Start recording via the Call API — independent of the <Stream> XML
    # above, so it doesn't interfere with the realtime audio bridge. Deferred
    # to a background task so a slow/failed Plivo request never delays the
    # answer response Plivo is waiting on.
    if call_uuid and voice_plivo.is_configured():
        callback_url = f"{settings.public_base_url.rstrip('/')}/voice/recording-callback"
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
    """Plivo posts here when a recording started in ``answer`` finishes
    processing — ``RecordingUrl`` points at Plivo's own hosted copy of the
    audio (no storage of our own needed), matched back to its Conversation
    via ``CallUUID``.
    """
    params = await _call_params(request)
    call_uuid = params.get("CallUUID") or params.get("call_uuid") or ""
    recording_url = params.get("RecordingUrl") or params.get("recording_url")
    duration_ms = params.get("RecordingDurationMs") or params.get("recording_duration_ms")

    duration_secs = None
    if duration_ms:
        try:
            duration_secs = float(duration_ms) / 1000.0
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
