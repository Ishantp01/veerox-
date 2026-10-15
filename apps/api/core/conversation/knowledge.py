"""Approved business knowledge (D4).

The engine may only answer factual questions from ``KnowledgeItem`` rows an
org admin has entered and left active. If nothing matches, the question is
"unknown" and goes to a follow-up / human handoff instead of a guess.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.conversation_engine import KnowledgeItem

KNOWLEDGE_KINDS = ("product", "pricing", "policy", "faq")

_TOKEN_RE = re.compile(r"[a-z0-9ऀ-ॿ]+")
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "is", "are", "of", "to", "for", "and", "or", "in", "on", "it", "do", "you",
        "me", "my", "your", "what", "how", "can", "i", "we", "this", "that", "with", "please",
        "hai", "hain", "kya", "ka", "ki", "ke", "ko", "se", "me", "mein", "aap", "mujhe", "batao",
        "bataiye", "ji", "ye", "yeh", "wo", "woh", "hota", "hoga", "kitna", "kitne", "kitni",
    }
)
# Customer phrasing -> kind, to rank pricing items first for price questions.
_PRICE_HINT = re.compile(r"(price|pricing|cost|fee|charge|rate|kitna|kitne|kitni|paisa|rupee|₹|कीमत|कितना)", re.I)

MAX_ITEMS = 2


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1 and t not in _STOPWORDS}


def _language_ok(item_language: str | None, language: str) -> bool:
    if item_language is None:
        return True
    if item_language == language:
        return True
    # Hinglish speakers read both English and Hindi-script answers fine.
    return language == "hinglish" and item_language in ("en", "hi", "hinglish")


async def find_knowledge(
    db: AsyncSession, org_id: UUID, text: str, language: str, *, limit: int = MAX_ITEMS
) -> list[KnowledgeItem]:
    """Active items whose title/keywords overlap the customer's words."""
    query_tokens = _tokens(text)
    if not query_tokens:
        return []
    items = (
        await db.execute(
            select(KnowledgeItem).where(KnowledgeItem.org_id == org_id, KnowledgeItem.active.is_(True))
        )
    ).scalars().all()
    price_question = bool(_PRICE_HINT.search(text))

    scored: list[tuple[int, KnowledgeItem]] = []
    for item in items:
        if not _language_ok(item.language, language):
            continue
        # Keywords count double: they are what the admin explicitly set as triggers.
        keyword_hits = len(query_tokens & _tokens(item.keywords or ""))
        title_hits = len(query_tokens & _tokens(item.title))
        score = keyword_hits * 2 + title_hits
        if score == 0:
            continue
        if price_question and item.kind == "pricing":
            score += 1
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]
