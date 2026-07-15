"""Plivo <-> OpenAI Realtime audio bridge (the voice channel's hot path).

Plivo opens a bidirectional WebSocket to ``/voice/stream`` (see
``channels/voice/webhook.py``) and streams the caller's audio as base64 mu-law
(8 kHz). This endpoint opens a second WebSocket to the OpenAI Realtime API and
runs two concurrent pumps:

  * Plivo  -> OpenAI : caller audio -> ``input_audio_buffer.append``
  * OpenAI -> Plivo  : model audio -> ``playAudio``, plus barge-in, tool
                       calls, and transcript persistence (see ``voice.adapter``).

Audio is mu-law 8 kHz on BOTH legs (Plivo ``contentType=audio/x-mulaw;rate=8000``
and OpenAI ``g711_ulaw``) so no resampling is required — the format-mismatch
pitfall in longrunning/operations/pitfalls.md is avoided by construction.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import structlog
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from apps.api.channels.voice import adapter as voice_adapter
from apps.api.config import settings
from apps.api.core.prompts import OUTBOUND_CALL_PROMPT, VOICE_APPEND, campaign_qualification_prompt
from apps.api.db.models.call_campaign import CallCampaign
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["voice"])

_OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"


async def _system_instructions(campaign_target_id: UUID | None) -> str:
    """Pick the campaign-scoped qualification script when this call was
    placed by the campaign dialer, else fall back to the fixed appointment-
    booking script used by the single-number Dial page."""
    if campaign_target_id is not None:
        async with AsyncSessionLocal() as db:
            target = await db.get(CampaignTarget, campaign_target_id)
            campaign = await db.get(CallCampaign, target.campaign_id) if target else None
        if campaign is not None:
            return f"{campaign_qualification_prompt(campaign.criteria).strip()}\n\n{VOICE_APPEND.strip()}"
    return f"{OUTBOUND_CALL_PROMPT.strip()}\n\n{VOICE_APPEND.strip()}"


def _session_update_event(instructions: str) -> dict[str, Any]:
    """Initial session config: mu-law I/O, server VAD, shared tool schemas.

    GA Realtime API shape (the beta shape — flat input_audio_format /
    output_audio_format / top-level voice / temperature — was removed on
    2026-05-12 and now fails the connection with beta_api_shape_disabled).
    """
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": instructions,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcmu"},
                    "turn_detection": {"type": "server_vad", "silence_duration_ms": 500},
                    "transcription": {"model": "whisper-1"},
                },
                "output": {
                    "format": {"type": "audio/pcmu"},
                    "voice": settings.openai_realtime_voice,
                },
            },
            "tools": voice_adapter.realtime_tools(),
            "tool_choice": "auto",
        },
    }


@router.websocket("/voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    """Bridge a single Plivo call to an OpenAI Realtime session."""
    await ws.accept()
    caller = ws.query_params.get("from", "unknown")
    call_uuid = ws.query_params.get("call_uuid", "")
    raw_campaign_target_id = ws.query_params.get("campaign_target_id")
    campaign_target_id = UUID(raw_campaign_target_id) if raw_campaign_target_id else None
    log = logger.bind(caller=caller, call_uuid=call_uuid)
    log.info("voice_stream_connected")

    conversation_id: Any = None
    try:
        user_id, conversation_id = await voice_adapter.open_voice_conversation(
            caller, call_uuid
        )
        state = voice_adapter.CallState(
            user_id=user_id,
            conversation_id=conversation_id,
            campaign_target_id=campaign_target_id,
        )
        if campaign_target_id is not None:
            # Proof the call actually connected — tells the hangup webhook
            # (campaign_dialer.handle_call_ended) not to re-dial this person
            # just because qualify_lead didn't fire before they hung up.
            await voice_adapter.attach_campaign_conversation(campaign_target_id, conversation_id)
        instructions = await _system_instructions(campaign_target_id)

        # No OpenAI-Beta header on the GA endpoint — sending it alongside a GA
        # model name causes the connection to be rejected outright.
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        url = f"{_OPENAI_REALTIME_URL}?model={settings.openai_realtime_model}"

        async with websockets.connect(
            url, additional_headers=headers, max_size=None
        ) as oai:
            await oai.send(json.dumps(_session_update_event(instructions)))
            # Make the agent greet first instead of waiting for the caller.
            await oai.send(
                json.dumps(
                    {
                        "type": "response.create",
                        "response": {
                            "instructions": (
                                "Greet the caller warmly in one short sentence "
                                "and ask how you can help."
                            ),
                        },
                    }
                )
            )
            log.info("openai_realtime_connected")

            async def pump_plivo_to_openai() -> None:
                try:
                    while True:
                        raw = await ws.receive_text()
                        msg = json.loads(raw)
                        event = msg.get("event")
                        if event == "media":
                            payload = (msg.get("media") or {}).get("payload")
                            if payload:
                                await oai.send(
                                    json.dumps(
                                        {
                                            "type": "input_audio_buffer.append",
                                            "audio": payload,
                                        }
                                    )
                                )
                        elif event == "start":
                            sid = (msg.get("start") or {}).get("streamId") or msg.get(
                                "streamId", ""
                            )
                            state.plivo_stream_id = sid
                            log.info("plivo_stream_start", stream_id=sid)
                        elif event == "stop":
                            log.info("plivo_stream_stop")
                            break
                except WebSocketDisconnect:
                    log.info("plivo_ws_disconnected")
                except Exception as exc:  # noqa: BLE001
                    log.warning("pump_plivo_error", error=str(exc))
                finally:
                    await oai.close()

            async def pump_openai_to_plivo() -> None:
                try:
                    async for raw in oai:
                        event = json.loads(raw)
                        await voice_adapter.handle_openai_event(
                            event, ws, oai, state, log
                        )
                except websockets.ConnectionClosed:
                    log.info("openai_ws_closed")
                except Exception as exc:  # noqa: BLE001
                    log.warning("pump_openai_error", error=str(exc))

            await asyncio.gather(pump_plivo_to_openai(), pump_openai_to_plivo())
    except Exception as exc:  # noqa: BLE001
        log.warning("voice_stream_error", error=str(exc))
    finally:
        if conversation_id is not None:
            await voice_adapter.close_voice_conversation(
                conversation_id, campaign_target_id=campaign_target_id
            )
        log.info("voice_stream_ended")
