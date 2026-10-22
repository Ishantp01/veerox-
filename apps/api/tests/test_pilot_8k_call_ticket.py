"""Phase 8K: an outbound call's answer is authorised by a single-use ticket minted when we dial.

Plivo's nonce carries no timestamp, so a remembered-nonce ledger needs an epoch of its own. A ticket gets its freshness from us:
it is minted at dial time, lives for minutes, is taken exactly once, and is bound to org and destination. These tests run the real
Redis commands and Lua through fakeredis (use the project's .venv); no server, tunnel, provider or paid request is involved.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

fakeredis = pytest.importorskip("fakeredis.aioredis")

from apps.api import pilot  # noqa: E402
from apps.api.channels.voice import failover, plivo_client, signature, webhook  # noqa: E402
from apps.api.channels.voice import realtime_bridge  # noqa: E402,F401
from apps.api.config import settings  # noqa: E402
from apps.api.core.org_credentials import PlivoCredentials  # noqa: E402
from apps.api.tests.test_admin_endpoints import ORG_ID, client  # noqa: E402,F401
from scripts import pilot_security_initialize as init  # noqa: E402

TOKEN = "unit-test-plivo-token"
BASE = "https://pilot.example.invalid"
OWNER = "+911234567890"
LINE = "+910000000001"
PATH = f"/voice/answer?org_id={ORG_ID}"


@pytest.fixture
async def world(monkeypatch: pytest.MonkeyPatch, test_engine: Any) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    redis = fakeredis.FakeRedis(decode_responses=True)
    for name, value in (
        ("environment", "pilot"), ("plivo_signature_mode", "auto"), ("pilot_allowed_numbers", OWNER), ("plivo_auth_token", TOKEN),
        ("public_base_url", BASE), ("secret_encryption_key", "synthetic-key"), ("pilot_max_outbound_calls", 1),
    ):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(signature, "get_redis_pool", lambda: redis)
    monkeypatch.setattr(webhook, "AsyncSessionLocal", async_sessionmaker(bind=test_engine, expire_on_commit=False))
    started: list[Any] = []
    monkeypatch.setattr(webhook, "start_precall_connect", lambda *a, **k: started.append(a))
    pilot.reset_outbound_counter()
    yield type("World", (), {"redis": redis, "started": started})
    pilot.reset_outbound_counter()
    await redis.aclose()


async def _ticketed(path: str = PATH, to: str = OWNER) -> str:
    return await signature.attach_call_ticket(BASE + path, to)


def _signed_post(client: Any, url: str, params: dict[str, str], nonce: str | None = None) -> Any:
    nonce = nonce or uuid.uuid4().hex
    parts = urlsplit(url)
    sig = signature.compute_signature(TOKEN, url, "POST", nonce, {k: [v] for k, v in params.items()})
    return client.post(
        parts.path + ("?" + parts.query if parts.query else ""),
        content=urlencode(params).encode(),
        headers={"X-Plivo-Signature-V3": sig, "X-Plivo-Signature-V3-Nonce": nonce, "Content-Type": "application/x-www-form-urlencoded"},
    )


ANSWER = {"CallUUID": "u-1", "From": LINE, "To": OWNER, "Direction": "outbound"}


# --- minting -------------------------------------------------------------------------------------------------------------------------


async def test_the_dialled_url_carries_a_random_ticket_that_is_stored_bound_and_short_lived(world: Any) -> None:
    url = await _ticketed()
    ticket = parse_qs(urlsplit(url).query)["ct"][0]
    assert re.fullmatch(r"[A-Za-z0-9_-]{30,}", ticket) and url.startswith(BASE + PATH + "&ct=")
    keys = [k async for k in world.redis.scan_iter(match="*:ticket:*")]
    assert len(keys) == 1 and 0 < await world.redis.ttl(keys[0]) <= settings.plivo_call_ticket_ttl_seconds
    stored = await world.redis.get(keys[0])
    assert re.fullmatch(r"[0-9a-f]{64}", stored) and OWNER[-10:] not in stored and ticket not in keys[0]  # no phone, no ticket in Redis
    assert await _ticketed() != url  # a different random ticket every call


async def test_urls_that_are_not_outbound_answers_and_a_signature_free_deployment_are_left_alone(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert await signature.attach_call_ticket(BASE + "/voice/answer", OWNER) == BASE + "/voice/answer"  # an inbound-style URL
    monkeypatch.setattr(settings, "environment", "production")
    assert await signature.attach_call_ticket(BASE + PATH, OWNER) == BASE + PATH
    assert [k async for k in world.redis.scan_iter(match="*")] == []  # nothing was written


async def test_production_default_never_touches_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError("redis must not be used when signatures are off")

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "plivo_signature_mode", "auto")
    monkeypatch.setattr(signature, "get_redis_pool", lambda: Boom())
    assert await signature.attach_call_ticket(BASE + PATH, OWNER) == BASE + PATH
    assert signature.issue_stream_params("c", OWNER, "o", None, "plivo") == {}


async def test_a_store_failure_stops_an_enforced_call_before_it_uses_the_allowance(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def down() -> Any:
        raise RuntimeError("redis down")

    monkeypatch.setattr(signature, "get_redis_pool", down)
    reached: list[str] = []

    async def provider(*a: Any, **k: Any) -> dict[str, Any]:
        reached.append("provider")
        return {"request_uuid": "x"}

    monkeypatch.setattr(plivo_client, "initiate_call", provider)
    creds = PlivoCredentials(auth_id="a", auth_token="b")
    with pytest.raises(signature.CallTicketUnavailable):
        await failover.initiate_call(creds, None, OWNER, BASE + PATH, plivo_from_number=LINE)
    assert reached == []
    monkeypatch.setattr(signature, "get_redis_pool", lambda: world.redis)
    await failover.initiate_call(creds, None, OWNER, BASE + PATH, plivo_from_number=LINE)  # the one allowed attempt is still available
    assert reached == ["provider"]


async def test_the_provider_receives_the_ticketed_url_and_the_requested_limits(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_outbound_time_limit_seconds", 120)
    monkeypatch.setattr(settings, "voice_outbound_ring_timeout_seconds", 25)
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"request_uuid": "mock"})

    monkeypatch.setattr(plivo_client, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await failover.initiate_call(PlivoCredentials(auth_id="a", auth_token="b"), None, OWNER, BASE + PATH, plivo_from_number=LINE)
    assert len(seen) == 1 and "ct=" in seen[0]["answer_url"] and seen[0]["time_limit"] == 120 and seen[0]["ring_timeout"] == 25


# --- consuming ---------------------------------------------------------------------------------------------------------------------------


async def test_a_genuine_answer_works_with_the_nonce_ledger_never_initialised(client: Any, world: Any) -> None:
    assert not await world.redis.exists(signature.state_key("nonces"))
    response = await _signed_post(client, await _ticketed(), ANSWER)
    assert response.status_code == 200 and "<Stream" in response.text and len(world.started) == 1


async def test_the_same_signed_request_cannot_be_replayed_even_with_a_fresh_nonce(client: Any, world: Any) -> None:
    url = await _ticketed()
    assert (await _signed_post(client, url, ANSWER)).status_code == 200
    assert (await _signed_post(client, url, ANSWER)).status_code == 403  # a retry or a capture: the ticket is gone
    assert (await _signed_post(client, url, ANSWER, nonce="fixed")).status_code == 403
    assert len(world.started) == 1


@pytest.mark.parametrize("how", ["no_ticket", "unknown_ticket", "other_destination", "other_org", "expired"])
async def test_an_answer_without_a_matching_live_ticket_is_refused_before_anything_happens(client: Any, world: Any, how: str) -> None:
    url = await _ticketed()
    params = dict(ANSWER)
    if how == "no_ticket":  # what every request captured before today looks like
        url = BASE + PATH
    elif how == "unknown_ticket":
        url = BASE + PATH + "&ct=" + "x" * 32
    elif how == "other_destination":
        params["To"] = "+919999999999"
    elif how == "other_org":
        url = url.replace(str(ORG_ID), str(uuid.uuid4()))
    else:
        async for key in world.redis.scan_iter(match="*:ticket:*"):
            await world.redis.delete(key)
    response = await _signed_post(client, url, params)
    assert response.status_code == 403 and response.content == b"" and world.started == []


async def test_an_unsigned_request_cannot_use_up_a_ticket(client: Any, world: Any) -> None:
    url = await _ticketed()
    unsigned = await client.post(urlsplit(url).path + "?" + urlsplit(url).query, content=urlencode(ANSWER).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert unsigned.status_code == 403
    assert (await _signed_post(client, url, ANSWER)).status_code == 200  # the genuine call is unharmed


async def test_inbound_answers_and_callbacks_still_need_the_ledger_and_so_stay_closed(client: Any, world: Any) -> None:
    inbound = await _signed_post(client, BASE + "/voice/answer", {"CallUUID": "u", "From": OWNER, "To": LINE})
    assert inbound.status_code == 403
    hangup = await _signed_post(client, BASE + "/voice/campaign-hangup?campaign_target_id=t", {"CallUUID": "u"})
    recording = await _signed_post(client, BASE + "/voice/recording-callback", {"CallUUID": "u", "RecordingUrl": "https://x/r"})
    assert hangup.status_code == 403 and recording.status_code == 403


# --- the stream, after the minimal initialisation ------------------------------------------------------------------------------------------


async def test_streams_only_initialisation_is_atomic_idempotent_and_leaves_the_nonce_ledger_alone(world: Any) -> None:
    key = signature.state_key("streams")
    assert await world.redis.eval(init.INITIALIZE_STREAMS, 1, key) == 1
    assert await world.redis.eval(init.INITIALIZE_STREAMS, 1, key) == 0
    assert await world.redis.hgetall(key) == {"__ready__": "1"} and await world.redis.ttl(key) == -1
    assert not await world.redis.exists(signature.state_key("nonces"))


def test_the_streams_only_form_does_not_ask_for_the_rotation_attestation() -> None:
    assert "confirm_all_plivo_signing_tokens_rotated" not in init.required_confirmations(True)
    assert "confirm_all_plivo_signing_tokens_rotated" in init.required_confirmations(False)
    assert len(init.required_confirmations(True)) == 2 and len(init.required_confirmations(False)) == 3


async def test_the_whole_outbound_path_admits_one_stream_and_refuses_a_duplicate_or_a_stranger(client: Any, world: Any) -> None:
    await world.redis.eval(init.INITIALIZE_STREAMS, 1, signature.state_key("streams"))
    response = await _signed_post(client, await _ticketed(), ANSWER)
    stream_url = response.text.split('streamTimeout="3600">')[1].split("</Stream>")[0].replace("&amp;", "&")
    q = {k: v[0] for k, v in parse_qs(urlsplit(stream_url).query).items()}
    args = (q["sig"], q["exp"], q["call_uuid"], q["from"], q["org_id"], None, "plivo")
    leases: list[Any] = []
    assert await signature.stream_allowed(*args, leases=leases)
    assert not await signature.stream_allowed(*args)  # a duplicate session
    assert not await signature.stream_allowed(q["sig"], q["exp"], "other-call", q["from"], q["org_id"], None, "plivo")
    assert await signature.release_stream(leases[0])
    assert await signature.stream_allowed(*args)  # a legitimate reconnect after a clean drop
