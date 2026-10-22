"""Execute security Lua with an isolated Redis emulator; never connect to a server."""

import asyncio
from dataclasses import replace
from uuid import UUID

import fakeredis.aioredis
import pytest
from starlette.websockets import WebSocketDisconnect

from apps.api.channels.voice import realtime_bridge, signature
from apps.api.config import settings
from apps.api.tests.test_pilot_8i_gate import (  # noqa: F401, F811
    CALL,
    NOW,
    ORG,
    PHONE,
    request,
)
from apps.api.tests.test_pilot_8i_gate import (
    gate as _gate,
)
from scripts.pilot_security_initialize import INITIALIZE

gate = _gate


@pytest.fixture
async def redis_gate(gate, monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await redis.sadd(signature.state_key("nonces"), "__ready__")
    await redis.hset(signature.state_key("streams"), "__ready__", "1")
    monkeypatch.setattr(signature, "get_redis_pool", lambda: redis)
    yield redis
    await redis.aclose()


def params():
    return signature.issue_stream_params(CALL, PHONE, ORG, None, "plivo")


def test_token_encoding_cannot_move_separators_between_claims(gate):
    assert signature.stream_token(
        "a", "b\x1fc", ORG, None, "plivo", NOW + 300
    ) != signature.stream_token("a\x1fb", "c", ORG, None, "plivo", NOW + 300)


async def acquire(p, leases=None):
    return await signature.stream_allowed(
        p["sig"], p["exp"], CALL, PHONE, ORG, None, "plivo", leases=leases
    )


async def test_concurrent_stream_admission_is_atomic(redis_gate):
    p = params()
    assert sum(await asyncio.gather(*(acquire(p) for _ in range(20)))) == 1
    assert await redis_gate.hlen(signature.state_key("streams")) == 2
    assert await redis_gate.ttl(signature.state_key("streams")) == -1


async def test_replacement_requires_owner_release_and_old_owner_cannot_release_new(redis_gate):
    p, leases, replacement = params(), [], []
    assert await acquire(p, leases)
    assert not await acquire(p)
    assert not await signature.release_stream(replace(leases[0], owner="impostor"))
    assert not await acquire(p)
    assert await signature.release_stream(leases[0])
    assert await acquire(p, replacement)
    assert not await signature.release_stream(leases[0])
    assert not await acquire(p)
    assert await signature.release_stream(replacement[0])


async def test_different_tokens_for_same_call_cannot_overlap(redis_gate, gate):
    assert await acquire(params())
    gate.now += 1
    assert not await acquire(params())


async def test_expired_token_cannot_reconnect_after_release(redis_gate, gate):
    p, leases = params(), []
    assert await acquire(p, leases)
    assert await signature.release_stream(leases[0])
    gate.now = int(p["exp"])
    assert not await acquire(p)


async def test_missing_state_and_capacity_fail_closed(redis_gate, monkeypatch):
    await redis_gate.delete(signature.state_key("streams"))
    assert not await acquire(params())
    await redis_gate.delete(signature.state_key("nonces"))
    assert (await signature.guard(request(), "answer")).status_code == 403
    await redis_gate.sadd(signature.state_key("nonces"), "__ready__")
    monkeypatch.setattr(settings, "plivo_nonce_capacity", 1)
    assert await signature.guard(request("first"), "answer") is None
    assert (await signature.guard(request("second"), "answer")).status_code == 403
    assert await redis_gate.scard(signature.state_key("nonces")) == 2
    await redis_gate.hset(signature.state_key("streams"), "__ready__", "1")
    monkeypatch.setattr(settings, "plivo_stream_capacity", 1)
    assert await acquire(params())
    p = signature.issue_stream_params("another-call", PHONE, ORG, None, "plivo")
    assert not await signature.stream_allowed(
        p["sig"], p["exp"], "another-call", PHONE, ORG, None, "plivo"
    )


async def test_concurrent_webhook_delivery_claimed_once(redis_gate):
    results = await asyncio.gather(*(signature.guard(request(), "answer") for _ in range(20)))
    assert sum(result is None for result in results) == 1
    assert all(result is None or result.status_code == 403 for result in results)
    assert await redis_gate.scard(signature.state_key("nonces")) == 2
    assert await redis_gate.ttl(signature.state_key("nonces")) == -1


async def test_nonce_survives_legacy_ttl_and_process_restart(redis_gate, gate):
    assert await signature.guard(request(), "answer") is None
    gate.now += 10 * settings.plivo_nonce_ttl_seconds
    # A new client/worker uses the same Redis ledger; no local replay state.
    assert (await signature.guard(request(), "answer")).status_code == 403


async def test_retry_new_nonce_accepted_but_same_nonce_never_reexecutes(redis_gate):
    assert await signature.guard(request("delivery-one"), "answer") is None
    assert await signature.guard(request("delivery-two"), "answer") is None
    assert (await signature.guard(request("delivery-one"), "answer")).status_code == 403


async def test_multi_signature_replay_cannot_switch_verifying_credential(redis_gate, monkeypatch):
    other = "synthetic-rotated-provider-token"

    def captured():
        req = request()
        alternate = signature.compute_signature(
            other,
            signature.public_url(req),
            "POST",
            "synthetic-nonce",
            {"CallUUID": [CALL], "To": [PHONE]},
        ).encode()
        req.scope["headers"] = [
            (name, value + b"," + alternate if name == b"x-plivo-signature-v3" else value)
            for name, value in req.scope["headers"]
        ]
        return req

    assert await signature.guard(captured(), "answer") is None
    monkeypatch.setattr(settings, "plivo_auth_token", other)
    assert (await signature.guard(captured(), "answer")).status_code == 403


async def test_release_outage_keeps_call_locked(redis_gate, monkeypatch):
    p, leases = params(), []
    assert await acquire(p, leases)

    def unavailable():
        raise RuntimeError("offline")

    monkeypatch.setattr(signature, "get_redis_pool", unavailable)
    assert not await signature.release_stream(leases[0])
    assert not await acquire(p)
    monkeypatch.setattr(signature, "get_redis_pool", lambda: redis_gate)
    assert not await acquire(p)


async def test_initializer_never_overwrites_existing_security_state(redis_gate):
    keys = [signature.state_key("nonces"), signature.state_key("streams")]
    assert await signature.guard(request(), "answer") is None
    assert await redis_gate.eval(INITIALIZE, 2, *keys) == 0
    assert (await signature.guard(request(), "answer")).status_code == 403
    await redis_gate.delete(*keys)
    assert await redis_gate.eval(INITIALIZE, 2, *keys) == 1
    assert await redis_gate.eval(INITIALIZE, 2, *keys) == 0
    assert await redis_gate.ttl(keys[0]) == await redis_gate.ttl(keys[1]) == -1


@pytest.mark.parametrize("change", ["call", "org", "token", "expiry"])
async def test_tampered_stream_binding_rejected_before_redis_claim(redis_gate, change):
    p = params()
    call, org = CALL, ORG
    if change == "call":
        call = "other-call"
    elif change == "org":
        org = "other-org"
    elif change == "expiry":
        p["exp"] = str(NOW + 301)
    else:
        p["sig"] = "tampered"
    assert not await signature.stream_allowed(p["sig"], p["exp"], call, PHONE, org, None, "plivo")
    assert await redis_gate.hlen(signature.state_key("streams")) == 1


class Socket:
    def __init__(self, p):
        self.query_params = {
            **p,
            "call_uuid": CALL,
            "from": PHONE,
            "org_id": ORG,
            "provider": "plivo",
        }
        self.closed = None

    async def accept(self):
        pass

    async def close(self, code=1000):
        self.closed = code


@pytest.mark.parametrize("preconnected", [False, True])
async def test_malformed_handshake_closes_ai_before_releasing_ownership(
    redis_gate, monkeypatch, preconnected
):
    monkeypatch.setattr(settings, "pilot_allowed_numbers", PHONE)
    connected = asyncio.Event()
    p = params()
    closed = []

    class AI:
        async def close(self):
            assert not await acquire(p)
            closed.append(True)

    async def resolve(*args):
        return UUID("00000000-0000-0000-0000-000000000001")

    async def claim(*args):
        if preconnected:
            connected.set()
            return AI(), "synthetic instructions"
        return None

    async def connect(*args):
        connected.set()
        return AI()

    class BrokenSocket(Socket):
        async def receive_text(self):
            await connected.wait()
            return "{invalid"

    monkeypatch.setattr(realtime_bridge, "_resolve_org_id", resolve)
    monkeypatch.setattr(realtime_bridge, "claim_precall_connect", claim)
    monkeypatch.setattr(realtime_bridge, "_connect_to_openai", connect)
    with pytest.raises(ValueError):
        await realtime_bridge.voice_stream(BrokenSocket(p))
    assert closed
    assert await acquire(p)


@pytest.mark.parametrize("ending", ["drop", "error", "cancel"])
async def test_socket_lifecycle_holds_ownership_through_cleanup(redis_gate, monkeypatch, ending):
    monkeypatch.setattr(settings, "pilot_allowed_numbers", PHONE)
    entered, finish, cleanup, cleanup_done = (asyncio.Event() for _ in range(4))

    async def handler(ws):
        entered.set()
        try:
            await finish.wait()
            if ending == "drop":
                raise WebSocketDisconnect()
            if ending == "error":
                raise RuntimeError("synthetic")
        finally:
            cleanup.set()
            await cleanup_done.wait()

    monkeypatch.setattr(realtime_bridge, "_authorized_voice_stream", handler)
    p = params()
    task = asyncio.create_task(realtime_bridge.voice_stream(Socket(p)))
    await asyncio.wait_for(entered.wait(), 2)
    duplicate = Socket(p)
    await realtime_bridge.voice_stream(duplicate)
    assert duplicate.closed == 1008
    if ending == "cancel":
        task.cancel()
    else:
        finish.set()
    await asyncio.wait_for(cleanup.wait(), 2)
    assert not await acquire(p)
    cleanup_done.set()
    await asyncio.gather(task, return_exceptions=True)
    replacement = []
    assert await acquire(p, replacement)
    assert await signature.release_stream(replacement[0])
