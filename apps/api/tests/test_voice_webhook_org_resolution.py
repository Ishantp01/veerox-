"""Tests for channels/voice/webhook.py's _resolve_org_by_last_contact —
the fallback used when a real inbound call is dialed on a number no org
owns as a dedicated line (multiple orgs sharing the platform default
number). It should route to whichever org this caller most recently had a
voice conversation with, instead of the static platform-default org.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.channels.voice import webhook
from apps.api.channels.voice.webhook import _resolve_org_by_last_contact
from apps.api.db.models import Conversation, Org, User

ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
CALLER = "+15550001111"


@pytest_asyncio.fixture(autouse=True)
async def _redirect_webhook_sessions(test_engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """_resolve_org_by_last_contact opens its own AsyncSessionLocal() rather
    than taking a ``db`` argument (it's called from a plain request handler,
    not one that threads a session through) — point it at the test engine
    so it shares the same in-memory SQLite the ``db_session`` fixture
    writes/reads through."""
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(webhook, "AsyncSessionLocal", session_factory)


async def _add_conversation(
    db: AsyncSession, org_id: uuid.UUID, phone: str, channel: str, started_at: datetime
) -> None:
    org = await db.get(Org, org_id)
    if org is None:
        org = Org(id=org_id, name=f"Org {org_id}")
        db.add(org)
        await db.flush()
    user = User(org_id=org_id, phone=phone)
    db.add(user)
    await db.flush()
    db.add(
        Conversation(org_id=org_id, user_id=user.id, channel=channel, started_at=started_at)
    )
    await db.commit()


async def test_no_history_returns_none(db_session: AsyncSession) -> None:
    assert await _resolve_org_by_last_contact(CALLER) is None


async def test_returns_the_only_org_with_voice_history(db_session: AsyncSession) -> None:
    await _add_conversation(db_session, ORG_A, CALLER, "voice", datetime.now(UTC))
    assert await _resolve_org_by_last_contact(CALLER) == str(ORG_A)


async def test_prefers_most_recent_org_over_older_one(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await _add_conversation(db_session, ORG_A, CALLER, "voice", now - timedelta(days=2))
    await _add_conversation(db_session, ORG_B, CALLER, "voice", now)
    assert await _resolve_org_by_last_contact(CALLER) == str(ORG_B)


async def test_ignores_whatsapp_only_history(db_session: AsyncSession) -> None:
    """A WhatsApp-only relationship shouldn't route an inbound *call* to
    that org — only a voice history counts here."""
    await _add_conversation(db_session, ORG_A, CALLER, "whatsapp", datetime.now(UTC))
    assert await _resolve_org_by_last_contact(CALLER) is None


async def test_matches_regardless_of_phone_formatting_differences(db_session: AsyncSession) -> None:
    """The caller param can arrive with different punctuation than what's
    stored — normalization must line them up (leading + kept, like the
    voice adapter stores it)."""
    await _add_conversation(db_session, ORG_A, "+15550002222", "voice", datetime.now(UTC))
    assert await _resolve_org_by_last_contact("+1 (555) 000-2222") == str(ORG_A)
