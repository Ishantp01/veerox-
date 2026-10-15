"""Process 3.0 — structured conversation memory with expiry rules.

Only the keys in ``MEMORY_KEYS`` are ever stored. Facts are scoped:

* ``call``       — expires after ``CALL_SCOPE_TTL`` (matches the 30 minute
                   "open conversation" window in core/agent.py).
* ``persistent`` — follows the customer across conversations, expires after
                   ``PERSISTENT_TTL`` and is refreshed every time it is
                   written again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.conversation.types import MEMORY_KEYS
from apps.api.db.models.conversation_engine import ConversationMemory

CALL_SCOPE_TTL = timedelta(minutes=30)
PERSISTENT_TTL = timedelta(days=90)

# Which scope each key lives in.
KEY_SCOPE: dict[str, str] = {
    "name": "persistent",
    "business_type": "persistent",
    "preferred_channel": "persistent",
    "callback_time": "call",
}

_MAX_VALUE_LEN = 200


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)  # SQLite hands back naive datetimes
    return value


async def load_memory(db: AsyncSession, org_id: UUID, user_id: UUID) -> dict[str, str]:
    """All unexpired facts for this customer, as ``{key: value}``."""
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(ConversationMemory).where(
                ConversationMemory.org_id == org_id, ConversationMemory.user_id == user_id
            )
        )
    ).scalars()
    facts: dict[str, str] = {}
    for row in rows:
        expires_at = _aware(row.expires_at)
        if expires_at is not None and expires_at <= now:
            continue
        facts[row.key] = row.value
    return facts


async def save_facts(
    db: AsyncSession, org_id: UUID, user_id: UUID, facts: dict[str, str]
) -> list[str]:
    """Upsert whitelisted facts; returns the keys written. Caller commits."""
    now = datetime.now(UTC)
    written: list[str] = []
    for key, value in facts.items():
        if key not in MEMORY_KEYS or not value or not value.strip():
            continue
        scope = KEY_SCOPE[key]
        expires_at = now + (PERSISTENT_TTL if scope == "persistent" else CALL_SCOPE_TTL)
        existing = (
            await db.execute(
                select(ConversationMemory).where(
                    ConversationMemory.org_id == org_id,
                    ConversationMemory.user_id == user_id,
                    ConversationMemory.scope == scope,
                    ConversationMemory.key == key,
                )
            )
        ).scalar_one_or_none()
        trimmed = value.strip()[:_MAX_VALUE_LEN]
        if existing is None:
            db.add(
                ConversationMemory(
                    org_id=org_id,
                    user_id=user_id,
                    scope=scope,
                    key=key,
                    value=trimmed,
                    expires_at=expires_at,
                )
            )
        else:
            existing.value = trimmed
            existing.expires_at = expires_at
        written.append(key)
    if written:
        await db.flush()
    return written
