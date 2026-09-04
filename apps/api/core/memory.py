"""Conversation memory — load chronological history, persist turns.

The loader applies *both* a count cap and a token-budget cap. The token cap
is the guard against the "context-blow on tool-heavy turn" failure described
in ``longrunning/operations/pitfalls.md`` — a single turn with many tool
results can otherwise push history past the model's context window.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from uuid import UUID

import structlog
import tiktoken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models.message import Message

logger = structlog.get_logger(__name__)

# Default ceiling for replayed history tokens. Sized to leave plenty of room
# in a 128k-context model for the system prompt, the fresh user turn, and any
# tool-call/response chatter the agent loop adds on top.
DEFAULT_TOKEN_BUDGET = 4000


@lru_cache(maxsize=1)
def _encoding() -> Any:
    """Lazily resolve and cache the encoding for ``settings.openai_chat_model``.

    Falls back to ``cl100k_base`` if tiktoken does not yet ship a mapping for
    the configured model (e.g. a newer o200k-family model on an old tiktoken
    build), so a model bump can't silently mis-tokenize.
    """
    try:
        return tiktoken.encoding_for_model(settings.openai_chat_model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    """Cheap token-count estimate for a single content string."""
    return int(len(_encoding().encode(text or "")))


async def load_last_n(
    db: AsyncSession,
    user_id: UUID,
    n: int = 20,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> list[dict[str, Any]]:
    """Load up to ``n`` recent messages for a user, oldest-first.

    Applies the token budget on top of the count cap: once both are
    satisfied the loader returns the chronological tail. Drops oldest first
    if the assembled context exceeds ``token_budget``.

    Args:
        db: Async SQLAlchemy session.
        user_id: User whose history we want.
        n: Max number of messages to consider.
        token_budget: Soft upper bound on combined ``content`` tokens.

    Returns:
        A list of ``{"role": ..., "content": ...}`` dicts ready for the
        OpenAI ``messages`` array.
    """
    stmt = (
        select(Message)
        .where(Message.user_id == user_id)
        .order_by(Message.created_at.desc())
        .limit(n)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()  # chronological order for the LLM

    # Apply token-budget cap by dropping from the oldest end until under budget.
    sized: list[tuple[Message, int]] = [(m, _count_tokens(m.content)) for m in rows]
    total = sum(tokens for _, tokens in sized)
    dropped = 0
    while total > token_budget and sized:
        _, dropped_tokens = sized.pop(0)
        total -= dropped_tokens
        dropped += 1

    if dropped:
        logger.info(
            "memory_truncated_for_budget",
            user_id=str(user_id),
            dropped=dropped,
            kept=len(sized),
            token_total=total,
            token_budget=token_budget,
        )

    return [{"role": m.role, "content": m.content} for m, _ in sized]


async def persist_turn(
    db: AsyncSession,
    conversation_id: UUID,
    user_id: UUID,
    org_id: UUID,
    channel: str,
    user_text: str,
    assistant_text: str,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    audio_secs: float | None = None,
) -> None:
    """Write the user + assistant rows for one turn in a single transaction.

    Token usage is attributed to the assistant row only (that row represents
    the LLM call that consumed the prompt and emitted the completion).
    ``audio_secs`` is for voice channels; pass ``None`` for text.
    """
    user_msg = Message(
        org_id=org_id,
        conversation_id=conversation_id,
        user_id=user_id,
        role="user",
        content=user_text,
        channel=channel,
        audio_secs=audio_secs,
    )
    asst_msg = Message(
        org_id=org_id,
        conversation_id=conversation_id,
        user_id=user_id,
        role="assistant",
        content=assistant_text,
        channel=channel,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )

    db.add_all([user_msg, asst_msg])
    await db.commit()

    logger.info(
        "turn_persisted",
        conversation_id=str(conversation_id),
        user_id=str(user_id),
        channel=channel,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        audio_secs=audio_secs,
    )
