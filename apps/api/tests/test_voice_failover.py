"""Tests for channels/voice/failover.py's initiate_call provider ordering —
in particular the new ``preferred_provider`` override used by the AI
Calling page's provider toggle (only shown when an org has a dedicated
number on both Plivo and Twilio)."""

from __future__ import annotations

import pytest

from apps.api.channels.voice import failover


@pytest.fixture(autouse=True)
def _both_providers_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(failover.plivo_client, "is_configured", lambda: True)
    monkeypatch.setattr(failover.twilio_client, "is_configured", lambda: True)


async def test_preferred_provider_twilio_is_tried_first_even_with_plivo_from_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a preference, a Plivo from_number would make Plivo primary —
    an explicit preferred_provider must override that inference."""
    attempted: list[str] = []

    async def _fake_plivo_call(*args: object, **kwargs: object) -> dict:
        attempted.append("plivo")
        return {"ok": True}

    async def _fake_twilio_call(*args: object, **kwargs: object) -> dict:
        attempted.append("twilio")
        return {"sid": "CA1"}

    monkeypatch.setattr(failover.plivo_client, "initiate_call", _fake_plivo_call)
    monkeypatch.setattr(failover.twilio_client, "initiate_call", _fake_twilio_call)

    _result, provider = await failover.initiate_call(
        "+15551234567",
        "https://example.com/voice/answer",
        plivo_from_number="+15550001111",
        twilio_from_number="+15550002222",
        preferred_provider="twilio",
    )

    assert provider == "twilio"
    assert attempted == ["twilio"]


async def test_preferred_provider_falls_back_to_the_other_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preference just reorders — it isn't a hard restriction, so a
    provider-wide outage on the chosen provider still fails over."""
    import httpx

    async def _fake_plivo_call(*args: object, **kwargs: object) -> dict:
        raise httpx.HTTPError("plivo down")

    async def _fake_twilio_call(*args: object, **kwargs: object) -> dict:
        return {"sid": "CA1"}

    monkeypatch.setattr(failover.plivo_client, "initiate_call", _fake_plivo_call)
    monkeypatch.setattr(failover.twilio_client, "initiate_call", _fake_twilio_call)

    _result, provider = await failover.initiate_call(
        "+15551234567",
        "https://example.com/voice/answer",
        preferred_provider="plivo",
    )

    assert provider == "twilio"


async def test_no_preference_keeps_automatic_from_number_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unchanged default behavior when the caller doesn't pass a preference
    (e.g. an org with only one dedicated number, or a campaign call)."""
    attempted: list[str] = []

    async def _fake_plivo_call(*args: object, **kwargs: object) -> dict:
        attempted.append("plivo")
        return {"ok": True}

    async def _fake_twilio_call(*args: object, **kwargs: object) -> dict:
        attempted.append("twilio")
        return {"sid": "CA1"}

    monkeypatch.setattr(failover.plivo_client, "initiate_call", _fake_plivo_call)
    monkeypatch.setattr(failover.twilio_client, "initiate_call", _fake_twilio_call)

    _result, provider = await failover.initiate_call(
        "+15551234567",
        "https://example.com/voice/answer",
        twilio_from_number="+15550002222",
    )

    assert provider == "twilio"
    assert attempted == ["twilio"]
