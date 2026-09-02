"""AgentCore — the channel-agnostic brain.

This module is the *only* place that knows about prompts, tool definitions,
the LLM, and how a turn is persisted. Voice and WhatsApp adapters are dumb
translators sitting *above* this layer; they call ``handle_turn`` and never
reach past it. That's the "one brain, two mouths" invariant — see
``longrunning/architecture/architecture.md``.
"""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.llm import ChatResult, ToolCall, chat_completion
from apps.api.core.memory import load_last_n, persist_turn
from apps.api.core.prompts import (
    KNOWN_USER_HINT,
    OUTBOUND_CALL_PROMPT,
    VOICE_APPEND,
    WHATSAPP_APPEND,
    current_datetime_block,
)
from apps.api.core.tools import DISPATCH_TABLE, TOOL_DEFINITIONS
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.models.conversation import Conversation
from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import get_redis_pool

logger = structlog.get_logger(__name__)


# A conversation is considered "open" if started within this window and not
# explicitly ended. Past the window we open a fresh conversation row — this
# keeps long gaps from accidentally rejoining stale context.
OPEN_CONVERSATION_WINDOW = timedelta(minutes=30)

# Max iterations of (LLM -> tool calls -> LLM -> ...) for a single turn.
# Five is generous: the realistic upper bound is two tool calls per turn
# (lookup_customer -> capture_lead) plus one final reply.
MAX_AGENT_ITERATIONS = 5

# Redis key + canned reply for the operator-controlled pause. AgentCore
# checks this on every turn; toggled from POST /admin/kill-switch.
KILL_SWITCH_KEY = "veerox:kill_switch"
KILL_SWITCH_REPLY = (
    "We're handling a quick issue on our end — back in a moment. "
    "Please try again shortly."
)

# Fallback when the model keeps requesting tool calls past the iteration cap.
ESCALATION_FALLBACK = (
    "I'm having trouble completing that right now. "
    "Let me connect you to a human agent."
)


Channel = Literal["voice", "whatsapp"]

# Set when this task's most recent handle_turn() call booked an appointment
# — read by channels/whatsapp/adapter.py to skip sending the model's own
# text reply, since book_appointment (core/tools.py) already sends its own
# WhatsApp template confirmation. Voice has no equivalent written record, so
# it keeps its spoken reply; this only affects the WhatsApp send. A
# ContextVar rather than a return-value field so handle_turn's `-> str`
# contract (relied on by cli/chat.py and the test suite) doesn't change —
# it's set deep inside the tool-dispatch loop and reads back correctly in
# the caller because both run in the same asyncio task.
_appointment_booked_ctx: ContextVar[bool] = ContextVar("_appointment_booked_ctx", default=False)


def appointment_booked_this_turn() -> bool:
    """Whether the just-completed ``handle_turn`` call on this task booked
    an appointment. Only meaningful immediately after awaiting handle_turn."""
    return _appointment_booked_ctx.get()


async def _system_prompt_for(db: AsyncSession, org_id: UUID, channel: Channel) -> str:
    """Compose the org's script (or the platform default) with the
    per-channel append block and the current-date/time grounding block.

    Strips both sides because ``prompts.py`` uses triple-quoted blocks that
    carry leading/trailing newlines — without this the joined system message
    ends up with stray blank lines that nudge the model toward overly formal
    output.
    """
    org = await db.get(Org, org_id)
    base = org.script if org is not None and org.script else OUTBOUND_CALL_PROMPT
    append = VOICE_APPEND if channel == "voice" else WHATSAPP_APPEND
    return f"{base.strip()}\n\n{current_datetime_block()}\n\n{append.strip()}"


async def _is_kill_switch_active() -> bool:
    """Return True when an operator has paused the agent via the dashboard."""
    redis = get_redis_pool()
    value = await redis.get(KILL_SWITCH_KEY)
    return value is not None


async def _get_or_open_conversation(
    db: AsyncSession,
    user_id: UUID,
    channel: Channel,
    org_id: UUID,
) -> Conversation:
    """Reuse the most-recent open conversation for the user+channel, else create.

    "Open" = started within ``OPEN_CONVERSATION_WINDOW`` and not yet ended.
    Beyond the window a fresh row is opened so stale context doesn't bleed
    into a new session.
    """
    threshold = datetime.now(UTC) - OPEN_CONVERSATION_WINDOW
    stmt = (
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.channel == channel,
            Conversation.started_at >= threshold,
            Conversation.ended_at.is_(None),
        )
        .order_by(Conversation.started_at.desc())
        .limit(1)
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    conversation = Conversation(org_id=org_id, user_id=user_id, channel=channel)
    db.add(conversation)
    await db.flush()  # populate conversation.id without committing yet
    return conversation


def _assistant_message_with_tool_calls(tool_calls: list[ToolCall]) -> dict[str, Any]:
    """Re-serialise our ``ToolCall`` dataclass back into the shape OpenAI wants.

    The chat-completions API requires the assistant turn that *requested* the
    tool calls to be present in the messages array on the follow-up call,
    along with each tool-result message keyed by ``tool_call_id``.
    """
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments_json},
            }
            for tc in tool_calls
        ],
    }


async def _dispatch_tool(
    db: AsyncSession,
    tool_call: ToolCall,
    user_id: UUID,
    org_id: UUID,
    channel: Channel,
    campaign_target_id: UUID | None = None,
    conversation_id: UUID | None = None,
    raw_message: str | None = None,
) -> dict[str, Any]:
    """Look up the handler, parse args, run it. Returns a JSON-serialisable dict.

    Unknown tool names and malformed JSON arguments are surfaced as a tool
    error result rather than raising — the agent loop should be able to
    recover (the model will see the error and try a different approach).
    """
    handler = DISPATCH_TABLE.get(tool_call.name)
    if handler is None:
        logger.warning("tool_unknown", name=tool_call.name)
        return {"status": "error", "reason": f"unknown_tool:{tool_call.name}"}

    try:
        args = json.loads(tool_call.arguments_json or "{}")
    except json.JSONDecodeError:
        logger.warning("tool_args_malformed", name=tool_call.name)
        return {"status": "error", "reason": "malformed_json_arguments"}

    if not isinstance(args, dict):
        return {"status": "error", "reason": "arguments_not_object"}

    # Inject caller context the LLM args can't carry — handlers absorb extras
    # via **_. campaign_target_id mirrors voice/adapter.py's
    # _dispatch_realtime_tool so qualify_lead can find the CampaignTarget row
    # to update on either channel. raw_message is the current turn's own
    # text, unaltered by the model — initiate_ai_call uses it as a
    # deterministic backstop against misreading "connect me to a human" as
    # a request to be called back (see core/tools.py).
    result = await handler(
        db,
        user_id=user_id,
        org_id=org_id,
        channel=channel,
        campaign_target_id=campaign_target_id,
        conversation_id=conversation_id,
        raw_message=raw_message,
        **args,
    )
    if not isinstance(result, dict):
        # Defensive: every handler advertises dict[str, Any] but be paranoid here
        # because malformed results would poison the next LLM iteration.
        return {"status": "ok", "result": str(result)}
    return result


async def _dispatch_tool_isolated(
    tool_call: ToolCall,
    user_id: UUID,
    org_id: UUID,
    channel: Channel,
    campaign_target_id: UUID | None,
    conversation_id: UUID | None,
    raw_message: str | None,
) -> dict[str, Any]:
    """Run one tool call on its own DB session — for fan-out alongside
    sibling tool calls in the same batch (see the parallel branch in
    ``handle_turn``). Each handler already commits its own work, and only
    ever references ``user_id``/``org_id``/``conversation_id`` by plain
    UUID rather than a shared ORM object, so an isolated session is safe.
    """
    async with AsyncSessionLocal() as tool_db:
        return await _dispatch_tool(
            tool_db,
            tool_call,
            user_id,
            org_id,
            channel,
            campaign_target_id=campaign_target_id,
            conversation_id=conversation_id,
            raw_message=raw_message,
        )


class AgentCore:
    """Channel-agnostic AI agent — one brain, two mouths.

    Stateless. Both Voice and WhatsApp adapters call ``handle_turn``; they
    never touch prompts, tool definitions, or memory directly.
    """

    async def handle_turn(
        self,
        db: AsyncSession,
        user_id: UUID,
        channel: Channel,
        input_text: str,
        campaign_target_id: UUID | None = None,
        org_id: UUID | None = None,
    ) -> str:
        """Process one conversational turn and return the assistant reply.

        Args:
            db: Open async session — caller (channel adapter or CLI) owns it.
            user_id: The user sending the message.
            channel: Transport hint — nudges response style; does NOT alter
                tools, memory, or routing.
            input_text: Transcribed (voice) or typed (whatsapp) user message.
            campaign_target_id: Set when this turn belongs to an open
                WhatsApp campaign outreach (see channels/whatsapp/adapter.py)
                — lets qualify_lead find the CampaignTarget row to update,
                mirroring how the voice realtime bridge threads the same id
                through _dispatch_realtime_tool.
            org_id: The org this turn belongs to, resolved by the caller
                (e.g. channels/whatsapp/adapter.py looks it up from the
                inbound message's WABA phone_number_id). Falls back to the
                platform default org when the caller doesn't have one to
                give — the CLI and older callers rely on this default.

        Returns:
            The assistant's reply as a plain string. The caller is responsible
            for transporting that string back over the channel.
        """
        org_id = org_id or UUID(settings.default_org_id)
        _appointment_booked_ctx.set(False)

        # Kill switch — operators can pause via dashboard. Check BEFORE any
        # spend on the LLM so a paused agent costs nothing.
        if await _is_kill_switch_active():
            logger.info("kill_switch_active_skipping_llm", user_id=str(user_id))
            return KILL_SWITCH_REPLY

        conversation = await _get_or_open_conversation(db, user_id, channel, org_id)

        # First turn since the campaign message went out — record which
        # conversation this target's reply landed in (idempotent: only ever
        # set once). Later turns in the same conversation keep resolving
        # campaign_target_id via the adapter's lookup until qualify_lead runs.
        if campaign_target_id is not None:
            target = await db.get(CampaignTarget, campaign_target_id)
            if target is not None and target.conversation_id is None:
                target.conversation_id = conversation.id
                await db.commit()

        history = await load_last_n(db, user_id)

        system_prompt = await _system_prompt_for(db, org_id, channel)
        if history:
            # We already have this user's history loaded — skip the redundant
            # lookup_customer round-trip the model would otherwise make on
            # nearly every turn of a returning conversation.
            system_prompt = f"{system_prompt}\n\n{KNOWN_USER_HINT.strip()}"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": input_text},
        ]

        # Agent loop: alternate (LLM call, tool-call dispatch) until the model
        # returns plain text or we hit the iteration ceiling.
        result: ChatResult | None = None
        assistant_text = ESCALATION_FALLBACK
        total_tokens_in = 0
        total_tokens_out = 0

        for iteration in range(MAX_AGENT_ITERATIONS):
            result = await chat_completion(messages, tools=TOOL_DEFINITIONS)
            total_tokens_in += result.tokens_in
            total_tokens_out += result.tokens_out

            if not result.tool_calls:
                assistant_text = result.content or ""
                break

            # Model wants to call tools. Echo its assistant turn (with the
            # tool_calls list) into history, then append a tool result for
            # each call, then loop for the model's follow-up reply.
            messages.append(_assistant_message_with_tool_calls(result.tool_calls))

            tool_names = [tc.name for tc in result.tool_calls]
            # Fan out concurrently only when every call in the batch targets
            # a different tool. Two calls to the *same* mutating tool (e.g.
            # book_appointment twice) must stay sequential on the shared
            # session — that's what lets the second call's conflict check
            # see the first call's just-committed write. Distinct tools
            # never touch each other's state, so isolated sessions are safe.
            if len(result.tool_calls) > 1 and len(set(tool_names)) == len(tool_names):
                tool_results = await asyncio.gather(
                    *(
                        _dispatch_tool_isolated(
                            tool_call,
                            user_id,
                            org_id,
                            channel,
                            campaign_target_id,
                            conversation.id,
                            input_text,
                        )
                        for tool_call in result.tool_calls
                    )
                )
            else:
                tool_results = []
                for tool_call in result.tool_calls:
                    tool_results.append(
                        await _dispatch_tool(
                            db,
                            tool_call,
                            user_id,
                            org_id,
                            channel,
                            campaign_target_id=campaign_target_id,
                            conversation_id=conversation.id,
                            raw_message=input_text,
                        )
                    )

            for tool_call, tool_result in zip(result.tool_calls, tool_results, strict=True):
                if tool_call.name == "book_appointment" and tool_result.get("status") == "ok":
                    _appointment_booked_ctx.set(True)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(tool_result),
                    }
                )

            logger.info(
                "agent_loop_iteration",
                iteration=iteration,
                tool_call_count=len(result.tool_calls),
                user_id=str(user_id),
            )
        else:
            # Loop exhausted without the model producing a final reply.
            logger.warning(
                "agent_loop_max_iterations",
                user_id=str(user_id),
                channel=channel,
            )

        await persist_turn(
            db,
            conversation_id=conversation.id,
            user_id=user_id,
            org_id=org_id,
            channel=channel,
            user_text=input_text,
            assistant_text=assistant_text,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
        )

        return assistant_text


# Module-level singleton — stateless, safe to share across requests.
agent_core = AgentCore()
