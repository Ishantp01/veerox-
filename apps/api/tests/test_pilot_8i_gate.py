"""Independent final gate checks against the current expiry/replay interface.

The strict replay checks deliberately fail when the present implementation
allows a captured stream token twice or a signed request after cache expiry.
No provider, live Redis, database, or paid request is used.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi import Request

from apps.api.channels.voice import signature
from apps.api.config import settings
from apps.api.core.privacy import privacy_processor

CALL, PHONE, ORG = "synthetic-call", "+911234567890", "synthetic-org"
NOW = 2_000_000_000


class ReplayStore:
    """Deterministic atomic Redis model; actual Lua is also tested with fakeredis."""
    def __init__(self):
        self.now = NOW
        self.nonces = {"__ready__"}
        self.streams = {"__ready__": "1"}

    async def set(self, key, value, nx=False, ex=None):
        tickets = self.__dict__.setdefault("tickets", {})
        if nx and key in tickets:
            return None
        tickets[key] = value
        return True

    async def getdel(self, key):
        return self.__dict__.setdefault("tickets", {}).pop(key, None)

    async def eval(self, script, count, key, *args):
        assert count == 1
        if script == signature.NONCE_CLAIM:
            if "__ready__" not in self.nonces:
                return -1
            if args[0] in self.nonces:
                return 0
            if len(self.nonces) >= int(args[1]) + 1:
                return -2
            self.nonces.add(args[0])
            return 1
        if script == signature.STREAM_CLAIM:
            if self.streams.get("__ready__") != "1":
                return -1
            if args[0] in self.streams:
                return 0
            if len(self.streams) >= int(args[2]) + 1:
                return -2
            self.streams[args[0]] = args[1]
            return 1
        assert script == signature.STREAM_RELEASE
        if self.streams.get(args[0]) != args[1]:
            return 0
        del self.streams[args[0]]
        return 1


@pytest.fixture
def gate(monkeypatch):
    store = ReplayStore()
    monkeypatch.setattr(settings, "environment", "pilot")
    monkeypatch.setattr(settings, "plivo_signature_mode", "auto")
    monkeypatch.setattr(settings, "public_base_url", "https://pilot.example.invalid")
    monkeypatch.setattr(settings, "plivo_auth_token", "synthetic-plivo-auth")
    monkeypatch.setattr(settings, "secret_encryption_key", "synthetic-stream-key")
    monkeypatch.setattr(settings, "plivo_stream_token_ttl_seconds", 300)
    monkeypatch.setattr(settings, "plivo_stream_token_max_uses", 2)
    monkeypatch.setattr(settings, "plivo_nonce_ttl_seconds", 3600)
    monkeypatch.setattr(signature, "get_redis_pool", lambda: store)
    monkeypatch.setattr(signature.time, "time", lambda: store.now)
    return store


def request(nonce="synthetic-nonce"):
    body = b"CallUUID=synthetic-call&To=%2B911234567890"
    # An inbound-style answer URL (no org/campaign): outbound answers are authorised by single-use tickets instead of the
    # nonce ledger these tests exercise (see test_pilot_8k_call_ticket.py).
    url = "https://pilot.example.invalid/voice/answer"
    signed = signature.compute_signature(
        "synthetic-plivo-auth", url, "POST", nonce, {"CallUUID": [CALL], "To": [PHONE]}
    )

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("127.0.0.1", 8002),
            "path": "/voice/answer",
            "query_string": b"",
            "headers": [
                (b"host", b"127.0.0.1:8002"),
                (b"x-plivo-signature-v3", signed.encode()),
                (b"x-plivo-signature-v3-nonce", nonce.encode()),
            ],
        },
        receive,
    )


async def test_expiry_and_missing_token_fail_closed(gate):
    params = signature.issue_stream_params(CALL, PHONE, ORG, None, "plivo")
    assert int(params["exp"]) == NOW + 300
    assert await signature.stream_allowed(
        params["sig"], params["exp"], CALL, PHONE, ORG, None, "plivo"
    )
    gate.now += 301
    assert not await signature.stream_allowed(
        params["sig"], params["exp"], CALL, PHONE, ORG, None, "plivo"
    )
    assert not await signature.stream_allowed(None, None, CALL, PHONE, ORG, None, "plivo")


@pytest.mark.parametrize("changed", ["call", "caller", "org", "campaign", "provider", "expiry"])
async def test_token_binds_every_claim(gate, changed):
    params = signature.issue_stream_params(CALL, PHONE, ORG, None, "plivo")
    values = [params["sig"], params["exp"], CALL, PHONE, ORG, None, "plivo"]
    index = {"expiry": 1, "call": 2, "caller": 3, "org": 4, "campaign": 5, "provider": 6}[changed]
    values[index] = str(NOW + 301) if changed == "expiry" else "different"
    assert not await signature.stream_allowed(*values)


async def test_nonce_replay_rejected_within_retention_window(gate):
    assert await signature.guard(request(), "answer") is None
    rejected = await signature.guard(request(), "answer")
    assert rejected is not None and rejected.status_code == 403


async def test_replay_store_outage_fails_closed(gate, monkeypatch):
    def unavailable():
        raise RuntimeError("offline")

    monkeypatch.setattr(signature, "get_redis_pool", unavailable)
    assert (await signature.guard(request(), "answer")).status_code == 403
    params = signature.issue_stream_params(CALL, PHONE, ORG, None, "plivo")
    assert not await signature.stream_allowed(
        params["sig"], params["exp"], CALL, PHONE, ORG, None, "plivo"
    )


def test_comparison_uses_constant_time_primitive(gate, monkeypatch):
    compare = Mock(wraps=signature.hmac.compare_digest)
    monkeypatch.setattr(signature.hmac, "compare_digest", compare)
    assert signature._same("same", "same")
    assert not signature._same("same", "different")
    assert not signature._same("same", "\u2603")
    assert compare.call_count == 3 and isinstance(compare.call_args.args[0], bytes)


def test_configured_public_base_ignores_untrusted_host(gate):
    assert (
        signature.public_url(request())
        == "https://pilot.example.invalid/voice/answer"
    )


def test_signed_urls_and_structured_token_fields_never_escape_logs(gate, monkeypatch):
    monkeypatch.setattr(settings, "log_privacy_enabled", True)
    params = signature.issue_stream_params(CALL, PHONE, ORG, None, "plivo")
    event = {
        "event": "WebSocket /voice/stream?sig=" + params["sig"] + "&from=" + PHONE,
        "sig": params["sig"],
        "nonce": "synthetic-private-nonce",
    }
    result = repr(privacy_processor(None, "info", event))
    assert (
        params["sig"] not in result
        and PHONE not in result
        and "synthetic-private-nonce" not in result
    )


def test_production_auto_preserves_legacy_behavior(gate, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    assert signature.effective_mode() == "off"
    assert signature.issue_stream_params(CALL, PHONE, ORG, None, "plivo") == {}


async def test_captured_stream_token_must_not_open_a_second_session(gate):
    params = signature.issue_stream_params(CALL, PHONE, ORG, None, "plivo")
    assert await signature.stream_allowed(
        params["sig"], params["exp"], CALL, PHONE, ORG, None, "plivo"
    )
    assert not await signature.stream_allowed(
        params["sig"], params["exp"], CALL, PHONE, ORG, None, "plivo"
    )


async def test_captured_signed_webhook_must_not_become_fresh_after_nonce_eviction(gate):
    assert await signature.guard(request(), "answer") is None
    gate.now += 3601
    rejected = await signature.guard(request(), "answer")
    assert rejected is not None and rejected.status_code == 403
