"""Voice adapter — Realtime event handling + tool dispatch + persistence.

The voice channel does NOT call ``AgentCore.handle_turn`` (that's the text /
chat-completions path). Instead the OpenAI Realtime session runs the spoken
conversation directly, and this adapter is the thin translator around it:

  * converts the shared ``TOOL_DEFINITIONS`` into Realtime tool schemas,
  * dispatches Realtime function calls through the SAME ``DISPATCH_TABLE`` the
    WhatsApp/text path uses (the "one brain, two mouths" invariant),
  * resolves the caller to a ``User`` + open ``Conversation``, and
  * streams transcripts to Postgres *during* the call (the Realtime session is
    ephemeral — see longrunning/operations/pitfalls.md).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.memory import persist_turn
from apps.api.core.tools import DISPATCH_TABLE, TOOL_DEFINITIONS
from apps.api.db.models.conversation import Conversation
from apps.api.db.models.user import User
from apps.api.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)


@dataclass
class CallState:
    """Mutable per-call context shared between the two audio pump tasks."""

    user_id: UUID
    conversation_id: UUID
    plivo_stream_id: str | None = None
    pending_user_transcript: str | None = None


def realtime_tools() -> list[dict[str, Any]]:
    """Convert chat-completions tool schemas to the flat Realtime format.

    Chat format nests under ``function``; the Realtime API wants
    ``name`` / ``description`` / ``parameters`` at the top level of each tool.
    """
    tools: list[dict[str, Any]] = []
    for definition in TOOL_DEFINITIONS:
        fn = definition.get("function", {})
        tools.append(
            {
                "type": "function",
                "name": fn.get("name"),
                "description": fn.get("description"),
                "parameters": fn.get("parameters"),
            }
        )
    return tools


def _normalize_phone(phone: str) -> str:
    """Strip everything but digits and a leading ``+`` (mirrors the other channels)."""
    return re.sub(r"[^\d+]", "", phone or "")


async def _get_or_create_user(db: AsyncSession, org_id: UUID, phone: str) -> User:
    stmt = select(User).where(User.org_id == org_id, User.phone == phone)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing
    user = User(org_id=org_id, phone=phone)
    db.add(user)
    await db.flush()
    return user


async def open_voice_conversation(caller: str) -> tuple[UUID, UUID]:
    """Resolve the caller to a User and open a fresh voice Conversation.

    Owns its own session and commits immediately so transcripts persisted
    later (during the call) have a committed parent row to attach to.
    """
    org_id = UUID(settings.default_org_id)
    phone = _normalize_phone(caller)
    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(db, org_id, phone)
        conversation = Conversation(org_id=org_id, user_id=user.id, channel="voice")
        db.add(conversation)
        await db.commit()
        return user.id, conversation.id


async def close_voice_conversation(conversation_id: UUID) -> None:
    """Mark the conversation ended when the call drops."""
    async with AsyncSessionLocal() as db:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is not None and conversation.ended_at is None:
            conversation.ended_at = datetime.now(UTC)
            await db.commit()


async def _dispatch_realtime_tool(
    name: str, arguments_json: str, user_id: UUID
) -> dict[str, Any]:
    """Run a Realtime function call through the shared ``DISPATCH_TABLE``.

    Mirrors ``apps.api.core.agent._dispatch_tool`` but owns a fresh DB session
    (the agent's request-scoped session doesn't exist on the voice path).
    """
    handler = DISPATCH_TABLE.get(name)
    if handler is None:
        logger.warning("voice_tool_unknown", name=name)
        return {"status": "error", "reason": f"unknown_tool:{name}"}
    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return {"status": "error", "reason": "malformed_json_arguments"}
    if not isinstance(args, dict):
        return {"status": "error", "reason": "arguments_not_object"}
    async with AsyncSessionLocal() as db:
        result = await handler(db, user_id=user_id, **args)
    return result if isinstance(result, dict) else {"status": "ok", "result": str(result)}


async def _persist_voice_turn(state: CallState, assistant_text: str) -> None:
    """Persist one (user transcript, assistant transcript) pair mid-call."""
    user_text = state.pending_user_transcript or "(voice)"
    async with AsyncSessionLocal() as db:
        await persist_turn(
            db,
            conversation_id=state.conversation_id,
            user_id=state.user_id,
            org_id=UUID(settings.default_org_id),
            channel="voice",
            user_text=user_text,
            assistant_text=assistant_text,
        )
    state.pending_user_transcript = None


async def _send_plivo(ws: WebSocket, message: dict[str, Any]) -> None:
    await ws.send_text(json.dumps(message))


async def handle_openai_event(
    event: dict[str, Any],
    plivo_ws: WebSocket,
    oai_ws: Any,
    state: CallState,
    log: Any,
) -> None:
    """Translate one OpenAI Realtime event into Plivo actions / tool calls."""
    etype = event.get("type")

    # GA renamed several beta event types (response.audio.delta ->
    # response.output_audio.delta, etc.) but docs are inconsistent on the
    # exact final names post-migration — accept both until confirmed live.
    if etype in ("response.audio.delta", "response.output_audio.delta"):
        delta = event.get("delta")
        if delta:
            await _send_plivo(
                plivo_ws,
                {
                    "event": "playAudio",
                    "media": {
                        "contentType": "audio/x-mulaw",
                        "sampleRate": 8000,
                        "payload": delta,
                    },
                },
            )

    elif etype == "input_audio_buffer.speech_started":
        # Barge-in: the caller started talking over the agent. Tell Plivo to
        # drop buffered playback so we don't talk over them.
        await _send_plivo(plivo_ws, {"event": "clearAudio"})

    elif etype == "conversation.item.input_audio_transcription.completed":
        state.pending_user_transcript = (event.get("transcript") or "").strip()
        log.info("voice_user_transcript", text=state.pending_user_transcript)

    elif etype in ("response.audio_transcript.done", "response.output_audio_transcript.done"):
        assistant_text = (event.get("transcript") or "").strip()
        if assistant_text:
            await _persist_voice_turn(state, assistant_text)
            log.info("voice_assistant_transcript", text=assistant_text)

    elif etype == "response.function_call_arguments.done":
        call_id = event.get("call_id")
        name = event.get("name") or ""
        args_json = event.get("arguments") or "{}"
        result = await _dispatch_realtime_tool(name, args_json, state.user_id)
        # Feed the tool result back into the session, then ask the model to
        # continue speaking with that result in context.
        await oai_ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result),
                    },
                }
            )
        )
        await oai_ws.send(json.dumps({"type": "response.create"}))
        log.info("voice_tool_dispatched", tool=name)

    elif etype == "error":
        log.warning("openai_realtime_error", error=event.get("error"))

    elif etype not in ("session.created", "session.updated", "response.created", "response.done"):
        log.info("voice_openai_event_unhandled", etype=etype)
