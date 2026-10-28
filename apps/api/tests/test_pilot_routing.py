# ruff: noqa: E501  (long assertion lines and a docstring in a routing test)
"""Pilot routing: every URL a staging call hands to Plivo, and the hangup route, point only at this process.

Offline and read-only: a fake HTTP client captures the Make-a-call request; nothing is dialled.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api import pilot
from apps.api.channels.voice import plivo_client, webhook
from apps.api.config import settings
from apps.api.core.org_credentials import PlivoCredentials
from apps.api.db.models import OrgPhoneNumber
from apps.api.tests.test_admin_endpoints import (  # noqa: F401
    ADMIN_HEADERS,
    ORG_ID,
    _seed_org,
    client,
    fake_redis,
)

STAGING = "https://stage-example.trycloudflare.com"
PRODUCTION_HOSTS = ("workassignai.com", "sslip.io")


def _host(url: str) -> str:
    return urlparse(url).hostname or ""


@pytest.fixture
def staging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "pilot")
    monkeypatch.setattr(settings, "public_base_url", STAGING)
    monkeypatch.setattr(settings, "voice_outbound_time_limit_seconds", 120)
    monkeypatch.setattr(settings, "voice_outbound_ring_timeout_seconds", 25)


def test_the_hangup_url_is_named_only_in_pilot_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_base_url", STAGING + "/")
    monkeypatch.setattr(settings, "environment", "pilot")
    assert pilot.outbound_hangup_url() == f"{STAGING}/voice/campaign-hangup"
    monkeypatch.setattr(settings, "environment", "production")
    assert pilot.outbound_hangup_url() is None  # production requests are exactly what they were
    monkeypatch.setattr(settings, "environment", "dev")
    assert pilot.outbound_hangup_url() is None


async def test_the_dashboard_dial_passes_the_hangup_url_only_in_pilot_mode(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, staging: None  # noqa: F811
) -> None:
    from apps.api.routers import admin as admin_module

    monkeypatch.setattr(settings, "pilot_allowed_numbers", "+919876543210")  # pilot mode refuses unlisted numbers up front
    await _seed_org(db_session)
    db_session.add(OrgPhoneNumber(org_id=ORG_ID, provider="plivo", phone_number="15550001111", is_default=True))
    await db_session.commit()
    monkeypatch.setattr(admin_module.voice_failover, "is_configured", lambda *a, **k: True)
    seen: dict[str, Any] = {}

    async def fake_initiate_call(*args: Any, **kwargs: Any) -> tuple[dict, str]:
        seen["args"], seen["kwargs"] = args, kwargs
        return {"request_uuid": "req-1"}, "plivo"

    monkeypatch.setattr(admin_module.voice_failover, "initiate_call", fake_initiate_call)
    monkeypatch.setattr(admin_module, "start_precall_connect", lambda *a, **k: None)

    response = await client.post("/admin/outbound/call", json={"to_phone": "+919876543210"}, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    answer_url = seen["args"][3]
    assert _host(answer_url) == _host(STAGING) and "/voice/answer" in answer_url
    assert seen["kwargs"]["hangup_url"] == f"{STAGING}/voice/campaign-hangup"

    monkeypatch.setattr(settings, "environment", "dev")
    await client.post("/admin/outbound/call", json={"to_phone": "+919876543210"}, headers=ADMIN_HEADERS)
    assert seen["kwargs"]["hangup_url"] is None


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"request_uuid": "req-1"}


async def test_the_plivo_request_names_only_staging_urls_and_the_provider_limits(
    monkeypatch: pytest.MonkeyPatch, staging: None
) -> None:
    sent: dict[str, Any] = {}

    async def fake_post(url: str, json: dict[str, Any], auth: Any) -> _FakeResponse:
        sent["url"], sent["payload"] = url, json
        return _FakeResponse()

    monkeypatch.setattr(plivo_client, "_http", SimpleNamespace(post=fake_post))
    await plivo_client.initiate_call(
        PlivoCredentials("id", "token"),
        "+919876543210",
        f"{STAGING}/voice/answer?org_id=o",
        "15550001111",
        hangup_url=pilot.outbound_hangup_url(),
    )
    payload = sent["payload"]
    assert payload["from"] == "15550001111"  # the caller ID is the only thing that is not staging
    for key in ("answer_url", "hangup_url"):
        assert _host(payload[key]) == _host(STAGING), key
        assert not any(h in payload[key] for h in PRODUCTION_HOSTS), key
    assert payload["answer_method"] == payload["hangup_method"] == "POST"
    assert payload["time_limit"] == 120 and payload["ring_timeout"] == 25  # enforced by Plivo itself
    assert not [k for k in payload if k.endswith("_url") and k not in ("answer_url", "hangup_url")]


def test_the_media_websocket_url_points_only_at_staging(staging: None) -> None:
    for url in (
        webhook._ws_stream_url("+919876543210", "call-1", org_id="o", provider="plivo"),
        webhook._ws_stream_base_url(),
    ):
        assert url.startswith("wss://" + _host(STAGING) + "/voice/stream"), url
        assert not any(h in url for h in PRODUCTION_HOSTS)
    query = parse_qs(urlparse(webhook._ws_stream_url("+919876543210", "call-1", org_id="o")).query)
    assert query["call_uuid"] == ["call-1"] and query["provider"] == ["plivo"]


async def test_a_hangup_event_without_a_campaign_triggers_no_workflow(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, staging: None  # noqa: F811
) -> None:
    from apps.api.workers import campaign_dialer

    monkeypatch.setattr(settings, "plivo_signature_mode", "off")

    async def must_not_run(*a: Any, **k: Any) -> None:
        raise AssertionError("a campaign workflow ran for a plain call's hangup event")

    monkeypatch.setattr(campaign_dialer, "handle_call_ended", must_not_run)
    response = await client.post(
        "/voice/campaign-hangup", data={"CallStatus": "completed", "Event": "Hangup", "CallUUID": "x"}
    )
    assert response.status_code == 204
