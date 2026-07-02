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
from fastapi import APIRouter, Request
from fastapi.responses import Response

from apps.api.config import settings

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


def _ws_stream_url(caller: str, call_uuid: str) -> str:
    """Build the ``wss://`` URL Plivo streams to, carrying caller context.

    Scheme is derived from ``PUBLIC_BASE_URL`` (https -> wss, http -> ws). The
    bridge reads ``from`` / ``call_uuid`` from the query string.
    """
    base = settings.public_base_url.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://") :]
    else:
        ws_base = "wss://" + base
    qs = urlencode({"from": caller, "call_uuid": call_uuid})
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
async def answer(request: Request) -> Response:
    """Return the Plivo XML executed when a call is answered."""
    params = await _call_params(request)
    caller = params.get("From") or params.get("from") or "unknown"
    call_uuid = params.get("CallUUID") or params.get("call_uuid") or ""

    ws_url = _ws_stream_url(caller, call_uuid)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Stream bidirectional="true" keepCallAlive="true" '
        'contentType="audio/x-mulaw;rate=8000" '
        f'streamTimeout="3600">{_xml_escape(ws_url)}</Stream>'
        "</Response>"
    )
    logger.info("plivo_answer_served", caller=caller, call_uuid=call_uuid)
    return Response(content=xml, media_type="application/xml")
