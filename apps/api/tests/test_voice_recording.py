"""Tests for Plivo call recording: adapter persistence + the recording
webhook. Outbound HTTP to Plivo itself isn't mocked here (the project has no
existing convention for that — outbound Plivo/Meta calls are gated by
``is_configured()`` and left untested when credentials are unset, same as
``initiate_call``); these tests cover the DB-facing half we own.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import adapter as adapter_module
from apps.api.config import settings
from apps.api.db.models import Conversation, Org, User
from apps.api.deps import get_db

ORG_ID = uuid.UUID(settings.default_org_id)


@pytest_asyncio.fixture
async def seeded_db(db_session: AsyncSession) -> AsyncSession:
    db_session.add(Org(id=ORG_ID, name="test-org"))
    await db_session.commit()
    return db_session


@pytest.fixture
def reuse_db(seeded_db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    """Make ``AsyncSessionLocal()`` (used by adapter.py's owned-session
    functions) yield the test session, same pattern as the WhatsApp adapter
    tests."""

    class _Ctx:
        async def __aenter__(self) -> AsyncSession:
            return seeded_db

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(adapter_module, "AsyncSessionLocal", lambda: _Ctx())
    return seeded_db


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    from apps.api.main import create_app

    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_open_voice_conversation_stores_call_uuid(reuse_db: AsyncSession) -> None:
    user_id, conversation_id = await adapter_module.open_voice_conversation(
        "+910000000050", "call-uuid-abc"
    )

    conversation = await reuse_db.get(Conversation, conversation_id)
    assert conversation is not None
    assert conversation.plivo_call_uuid == "call-uuid-abc"
    assert conversation.user_id == user_id


async def test_save_call_recording_updates_matching_conversation(
    reuse_db: AsyncSession,
) -> None:
    user = User(org_id=ORG_ID, phone="+910000000051")
    reuse_db.add(user)
    await reuse_db.flush()
    conversation = Conversation(
        org_id=ORG_ID, user_id=user.id, channel="voice", plivo_call_uuid="call-uuid-xyz"
    )
    reuse_db.add(conversation)
    await reuse_db.commit()

    found = await adapter_module.save_call_recording(
        "call-uuid-xyz", "https://media.plivo.com/rec-1.mp3", 42.5
    )

    assert found is True
    await reuse_db.refresh(conversation)
    assert conversation.recording_url == "https://media.plivo.com/rec-1.mp3"
    assert conversation.recording_duration_secs == 42.5


async def test_save_call_recording_returns_false_when_no_match(
    reuse_db: AsyncSession,
) -> None:
    found = await adapter_module.save_call_recording(
        "no-such-call-uuid", "https://media.plivo.com/rec-2.mp3", 10.0
    )
    assert found is False


async def test_recording_callback_endpoint_updates_conversation(
    client: AsyncClient, reuse_db: AsyncSession
) -> None:
    user = User(org_id=ORG_ID, phone="+910000000052")
    reuse_db.add(user)
    await reuse_db.flush()
    conversation = Conversation(
        org_id=ORG_ID, user_id=user.id, channel="voice", plivo_call_uuid="call-uuid-cb"
    )
    reuse_db.add(conversation)
    await reuse_db.commit()

    response = await client.post(
        "/voice/recording-callback",
        data={
            "CallUUID": "call-uuid-cb",
            "RecordingUrl": "https://media.plivo.com/rec-3.mp3",
            "RecordingDurationMs": "5000",
        },
    )

    assert response.status_code == 204
    await reuse_db.refresh(conversation)
    assert conversation.recording_url == "https://media.plivo.com/rec-3.mp3"
    assert conversation.recording_duration_secs == 5.0


async def test_recording_callback_endpoint_ignores_missing_fields(
    client: AsyncClient,
) -> None:
    response = await client.post("/voice/recording-callback", data={"CallUUID": "some-call"})
    assert response.status_code == 204
