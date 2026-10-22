"""Phase 8H: the single controlled outbound call cannot turn into two provider requests.

Ways a second request could appear, each proven closed offline with a mock provider:
a Plivo error falling back to Twilio, two requests arriving at once, an AI tool asking for a callback during the call,
and the provider parameters themselves. The provider is a MockTransport: nothing leaves the machine.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from apps.api import pilot
from apps.api.channels.voice import failover, plivo_client, twilio_client
from apps.api.config import settings
from apps.api.core.org_credentials import PlivoCredentials

PLIVO = PlivoCredentials(auth_id="mock", auth_token="mock")
OWNER = "+911234567890"


class _Seen(list):  # type: ignore[type-arg]
    """Every request that would have reached a provider, plus how the fake provider should answer."""

    behaviour: dict[str, int]


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> _Seen:
    seen = _Seen()
    behaviour = {"status": 200}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append({"host": req.url.host, "body": json.loads(req.content) if req.headers.get("content-type", "").startswith("application/json") else {}})
        return httpx.Response(behaviour["status"], json={"request_uuid": "mock"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(plivo_client, "_http", client)
    monkeypatch.setattr(twilio_client, "_http", client)
    seen.behaviour = behaviour
    pilot.reset_outbound_counter()
    monkeypatch.setattr(settings, "environment", "pilot")
    monkeypatch.setattr(settings, "pilot_allowed_numbers", OWNER)
    monkeypatch.setattr(settings, "pilot_max_outbound_calls", 1)
    monkeypatch.setattr(settings, "voice_outbound_time_limit_seconds", 120)
    monkeypatch.setattr(settings, "voice_outbound_ring_timeout_seconds", 25)
    yield seen
    pilot.reset_outbound_counter()


async def _call() -> tuple[Any, str]:
    # Twilio is not configured in the pilot file: the second creds argument is None, as resolve_twilio_credentials returns.
    return await failover.initiate_call(PLIVO, None, OWNER, "https://x.invalid/voice/answer", plivo_from_number="+910000000001")


async def test_a_plivo_error_does_not_fail_over_to_another_provider(provider: _Seen) -> None:
    provider.behaviour["status"] = 500
    with pytest.raises(httpx.HTTPStatusError):
        await _call()
    assert [r["host"] for r in provider] == ["api.plivo.com"]  # one request, to one provider


async def test_two_simultaneous_requests_reach_the_provider_once(provider: _Seen) -> None:
    results = await asyncio.gather(_call(), _call(), return_exceptions=True)
    assert len(provider) == 1
    assert sum(isinstance(r, pilot.PilotBlocked) for r in results) == 1 and sum(isinstance(r, tuple) for r in results) == 1


async def test_a_failed_attempt_still_uses_up_the_single_call(provider: _Seen) -> None:
    provider.behaviour["status"] = 500
    with pytest.raises(httpx.HTTPStatusError):
        await _call()
    provider.behaviour["status"] = 200
    with pytest.raises(pilot.PilotBlocked, match="limit of 1"):
        await _call()  # money may have been spent on the first: no automatic second try
    assert len(provider) == 1


async def test_the_requested_limits_are_in_the_request_the_provider_receives(provider: _Seen) -> None:
    await _call()
    body = provider[0]["body"]
    assert body["time_limit"] == 120 and body["ring_timeout"] == 25
    assert isinstance(body["time_limit"], int) and isinstance(body["ring_timeout"], int)
    assert set(body) == {"from", "to", "answer_url", "answer_method", "time_limit", "ring_timeout"}  # no hangup/fallback URL that could trigger more traffic


async def test_the_second_call_from_an_ai_tool_is_blocked_by_the_same_counter(provider: _Seen) -> None:
    """The in-call callback tool and the admin endpoint share failover.initiate_call, so they share the one-call counter."""
    import inspect

    from apps.api.core import tools

    assert "voice_failover.initiate_call" in inspect.getsource(tools)
    await _call()
    with pytest.raises(pilot.PilotBlocked):
        await failover.initiate_call(PLIVO, None, OWNER, "https://x.invalid/voice/answer", plivo_from_number="+910000000001")
    assert len(provider) == 1


def test_every_code_path_that_places_a_call_goes_through_the_guarded_function() -> None:
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name in {"failover.py", "plivo_client.py", "twilio_client.py"}:
            continue
        for match in re.finditer(r"\b(plivo_client|twilio_client)\.initiate_call\(", path.read_text(encoding="utf8")):
            offenders.append(path.name)
    assert offenders == [], f"a call is placed without the allowlist and one-call guard: {offenders}"
