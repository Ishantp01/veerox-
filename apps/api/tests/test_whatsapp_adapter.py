"""Tests for the WhatsApp adapter — Meta envelope -> AgentCore -> reply."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.whatsapp import adapter as adapter_module
from apps.api.db.models import Org

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class _FakeRedis:
    """Minimal Redis SET NX EX surface used by the adapter's idempotency claim."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True


@pytest_asyncio.fixture
async def seeded_db(db_session: AsyncSession) -> AsyncSession:
    """Seed the default Org row the adapter assumes exists."""
    db_session.add(Org(id=ORG_ID, name="test-org"))
    await db_session.commit()
    return db_session


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(adapter_module, "get_redis_pool", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _pin_default_org(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api import config as config_module

    monkeypatch.setattr(config_module.settings, "default_org_id", str(ORG_ID))


@pytest.fixture
def reuse_db(
    monkeypatch: pytest.MonkeyPatch,
    seeded_db: AsyncSession,
) -> AsyncSession:
    """Make ``AsyncSessionLocal()`` yield the test session."""

    class _Ctx:
        async def __aenter__(self) -> AsyncSession:
            return seeded_db

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(adapter_module, "AsyncSessionLocal", lambda: _Ctx())
    return seeded_db


def _text_payload(msg_id: str, from_phone: str, body: str) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "id": msg_id,
                                    "from": from_phone,
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_text_message_triggers_agent_and_send(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
    reuse_db: AsyncSession,
) -> None:
    handle_calls: list[dict[str, Any]] = []
    send_calls: list[tuple[str, str]] = []
    mark_calls: list[str] = []

    async def fake_handle_turn(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        channel: str,
        input_text: str,
        campaign_target_id: uuid.UUID | None = None,
        org_id: uuid.UUID | None = None,
    ) -> str:
        handle_calls.append({"user_id": user_id, "channel": channel, "text": input_text})
        return "hello back"

    async def fake_send_text(
        to: str, body: str, phone_number_id: str | None = None
    ) -> dict[str, Any]:
        send_calls.append((to, body))
        return {"messages": [{"id": "wamid.out"}]}

    async def fake_mark_read(msg_id: str, typing: bool = False, phone_number_id: str | None = None) -> None:
        mark_calls.append(msg_id)

    # Patch where the adapter looks them up (it imports the names from the
    # bound modules at call time).
    monkeypatch.setattr(adapter_module.agent_core, "handle_turn", fake_handle_turn)
    monkeypatch.setattr(adapter_module.wa_client, "send_text", fake_send_text)
    monkeypatch.setattr(adapter_module.wa_client, "mark_read", fake_mark_read)

    payload = _text_payload("wamid.abc", "919876543210", "hello there")
    await adapter_module.process_inbound(payload)

    assert len(handle_calls) == 1
    assert handle_calls[0]["channel"] == "whatsapp"
    assert handle_calls[0]["text"] == "hello there"
    assert send_calls == [("919876543210", "hello back")]
    assert mark_calls == ["wamid.abc"]


@pytest.mark.asyncio
async def test_duplicate_message_id_is_skipped_on_second_delivery(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
    reuse_db: AsyncSession,
) -> None:
    """Meta retries on missed ACKs — the second delivery must be a no-op."""
    handle_calls: list[str] = []
    send_calls: list[tuple[str, str]] = []

    async def fake_handle_turn(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        channel: str,
        input_text: str,
        campaign_target_id: uuid.UUID | None = None,
        org_id: uuid.UUID | None = None,
    ) -> str:
        handle_calls.append(input_text)
        return "echo: " + input_text

    async def fake_send_text(
        to: str, body: str, phone_number_id: str | None = None
    ) -> dict[str, Any]:
        send_calls.append((to, body))
        return {"messages": [{"id": "wamid.out"}]}

    async def fake_mark_read(_: str, typing: bool = False, phone_number_id: str | None = None) -> None:
        return None

    monkeypatch.setattr(adapter_module.agent_core, "handle_turn", fake_handle_turn)
    monkeypatch.setattr(adapter_module.wa_client, "send_text", fake_send_text)
    monkeypatch.setattr(adapter_module.wa_client, "mark_read", fake_mark_read)

    payload = _text_payload("wamid.dup", "919876543210", "hi")
    await adapter_module.process_inbound(payload)
    await adapter_module.process_inbound(payload)  # duplicate

    assert handle_calls == ["hi"]
    assert send_calls == [("919876543210", "echo: hi")]


@pytest.mark.asyncio
async def test_status_only_payload_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: _FakeRedis,
    reuse_db: AsyncSession,
) -> None:
    """Delivery / read-receipt callbacks have no messages[] — return early."""
    called = {"agent": False, "send": False}

    async def fake_handle_turn(*_: Any, **__: Any) -> str:
        called["agent"] = True
        return ""

    async def fake_send_text(*_: Any, **__: Any) -> dict[str, Any]:
        called["send"] = True
        return {}

    monkeypatch.setattr(adapter_module.agent_core, "handle_turn", fake_handle_turn)
    monkeypatch.setattr(adapter_module.wa_client, "send_text", fake_send_text)

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "statuses": [{"id": "wamid.x", "status": "delivered"}],
                        },
                    }
                ]
            }
        ],
    }
    await adapter_module.process_inbound(payload)
    assert called == {"agent": False, "send": False}


@pytest.mark.asyncio
async def test_find_open_campaign_target_resolves_whatsapp_target_on_mixed_campaign(
    seeded_db: AsyncSession,
) -> None:
    """Regression guard: a campaign holding both voice and WhatsApp targets
    (CallCampaign.channel == "mixed") must still resolve an inbound WhatsApp
    reply to that contact's WhatsApp target specifically — not miss it by
    filtering on the now-display-only CallCampaign.channel, and not
    accidentally match the same contact's voice target instead."""
    from apps.api.db.models.call_campaign import CallCampaign
    from apps.api.db.models.campaign_target import CampaignTarget

    campaign = CallCampaign(org_id=ORG_ID, name="Mixed campaign", criteria="n/a", channel="mixed")
    seeded_db.add(campaign)
    await seeded_db.flush()

    phone = "+919876543210"
    voice_target = CampaignTarget(
        campaign_id=campaign.id, org_id=ORG_ID, phone=phone,
        channel="voice", status="completed", qualified=None,
    )
    wa_target = CampaignTarget(
        campaign_id=campaign.id, org_id=ORG_ID, phone=phone,
        channel="whatsapp", status="completed", qualified=None,
    )
    seeded_db.add_all([voice_target, wa_target])
    await seeded_db.commit()

    resolved_id = await adapter_module._find_open_campaign_target(seeded_db, phone)

    assert resolved_id == wa_target.id
    assert resolved_id != voice_target.id


@pytest.mark.asyncio
async def test_find_open_campaign_target_none_when_only_voice_target_open(
    seeded_db: AsyncSession,
) -> None:
    from apps.api.db.models.call_campaign import CallCampaign
    from apps.api.db.models.campaign_target import CampaignTarget

    campaign = CallCampaign(
        org_id=ORG_ID, name="Voice-only campaign", criteria="n/a", channel="voice"
    )
    seeded_db.add(campaign)
    await seeded_db.flush()

    phone = "+919876543211"
    seeded_db.add(
        CampaignTarget(
            campaign_id=campaign.id, org_id=ORG_ID, phone=phone,
            channel="voice", status="completed", qualified=None,
        )
    )
    await seeded_db.commit()

    resolved_id = await adapter_module._find_open_campaign_target(seeded_db, phone)

    assert resolved_id is None
