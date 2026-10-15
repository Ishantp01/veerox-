"""Tables for the Conversation Intelligence Engine (core/conversation/).

* ``ConversationTurn``  — one row per engine run (live or shadow): state,
  intent, scenario, confidence, knowledge used, action and final reply.
* ``ConversationMemory`` — structured facts (name, business type, preferred
  channel, callback time) with an expiry.
* ``KnowledgeItem``      — approved business knowledge the engine may answer from.

Every row is org-scoped; every query must filter by ``org_id``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (Index("ix_conversation_turns_org_conv_created", "org_id", "conversation_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    # "live" = reply was sent to the customer; "shadow" = log-only.
    mode: Mapped[str] = mapped_column(String(8), nullable=False, server_default="live")
    user_text: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scenario: Mapped[str | None] = mapped_column(String(32), nullable=True)
    emotion: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # "rules" or "llm" — which path produced the classification.
    source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    knowledge_ids: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    action_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reply_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the engine bailed out to the legacy script path (timeout,
    # invalid_output, low_confidence, error, ...). NULL = engine answered.
    fallback_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class ConversationMemory(Base):
    __tablename__ = "conversation_memory"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "scope", "key", name="uq_conversation_memory_fact"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "call" facts only matter within one conversation window; "persistent"
    # facts follow the user across conversations until they expire.
    scope: Mapped[str] = mapped_column(String(12), nullable=False)
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # product | pricing | policy | faq
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Space/comma separated trigger words; matched against the customer text
    # in addition to title words (core/conversation/knowledge.py).
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "hi" | "en" | "hinglish" | None (any language).
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
