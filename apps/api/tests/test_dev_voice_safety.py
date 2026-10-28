# ruff: noqa: E501, F811  (long assertion lines; fixtures are imported by name)
"""Local-development safety: an optional dial allowlist, a voice preflight, failed TTS reported as failure (never as
spoken), and the audio format / delivery path to Plivo. Offline: fake sockets and a fake HTTP client, no network, no call."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from apps.api import pilot
from apps.api.channels.voice import adapter, config_check, elevenlabs_client
from apps.api.channels.voice.elevenlabs_client import VoiceCheck
from apps.api.config import Settings, settings
from apps.api.db.models import OrgPhoneNumber, User
from apps.api.tests.test_admin_endpoints import (  # noqa: F401
    ADMIN_HEADERS,
    ORG_ID,
    _seed_org,
    client,
    fake_redis,
)
from apps.api.tests.test_voice_pipeline_diag import SPOKEN, _agent_turn, _Ws, real_tts  # noqa: F401
from apps.api.tests.test_voice_tts_turns import Call, fakes  # noqa: F401  (autouse fixture)

TESTER = "+919876543210"
STRANGER = "+919811112222"


# --- defaults ------------------------------------------------------------------------------------------------------


def test_new_settings_default_to_no_restriction_and_no_check() -> None:
    assert Settings.model_fields["voice_dial_allowlist"].default == ""
    assert Settings.model_fields["voice_preflight_voice_check"].default is False


# --- the dial allowlist --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["dev", "production", ""])
def test_an_empty_allowlist_restricts_nothing_in_any_environment(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "voice_dial_allowlist", "")
    pilot.check_number_allowed(STRANGER, what="call")  # no exception


def test_a_set_allowlist_refuses_every_other_number_but_not_the_listed_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "voice_dial_allowlist", f"{TESTER}, 99990 00000")
    pilot.check_number_allowed(TESTER, what="call")
    pilot.check_number_allowed("9876543210", what="sms")  # country-code formatting does not matter
    with pytest.raises(pilot.PilotBlocked):
        pilot.check_number_allowed(STRANGER, what="call")
    with pytest.raises(pilot.PilotBlocked):
        pilot.check_number_allowed(STRANGER, what="whatsapp")


def test_pilot_mode_still_uses_its_own_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "pilot")
    monkeypatch.setattr(settings, "voice_dial_allowlist", STRANGER)  # ignored in pilot mode
    monkeypatch.setattr(settings, "pilot_allowed_numbers", TESTER)
    pilot.check_number_allowed(TESTER, what="call")
    with pytest.raises(pilot.PilotBlocked):
        pilot.check_number_allowed(STRANGER, what="call")


def test_the_refusal_log_never_carries_the_full_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "voice_dial_allowlist", TESTER)
    with capture_logs() as captured, pytest.raises(pilot.PilotBlocked):
        pilot.check_number_allowed(STRANGER, what="call")
    assert "9811112222" not in json.dumps(captured, default=str) and "***2222" in json.dumps(captured, default=str)


# --- the voice check against the provider ----------------------------------------------------------------------------

GOOD = "IP6hMX5nniiLQScQfOs4"


class _Http:
    def __init__(self, status: int | None = None, error: Exception | None = None) -> None:
        self.status, self.error, self.calls = status, error, 0

    async def get(self, url: str, headers: dict[str, str]) -> Any:
        self.calls += 1
        if self.error:
            raise self.error
        assert "xi-api-key" in headers and url.endswith(f"/voices/{GOOD}")
        return SimpleNamespace(status_code=self.status)


@pytest.fixture(autouse=True)
def _fresh_voice_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    elevenlabs_client._voice_check_cache.clear()
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")


@pytest.mark.parametrize(
    "status, ok, reason",
    [(200, True, "ok"), (400, False, "voice_not_found"), (404, False, "voice_not_found"), (401, False, "unauthorized"), (503, None, "http_503")],
)
async def test_voice_validation_maps_the_providers_answer(status: int, ok: bool | None, reason: str) -> None:
    assert await elevenlabs_client.validate_voice(GOOD, client=_Http(status)) == VoiceCheck(ok, reason)


async def test_an_unreachable_provider_is_not_an_invalid_voice() -> None:
    assert await elevenlabs_client.validate_voice(GOOD, client=_Http(error=OSError("down"))) == VoiceCheck(None, "unreachable")


async def test_only_definitive_answers_are_cached() -> None:
    http = _Http(200)
    await elevenlabs_client.validate_voice(GOOD, client=http)
    await elevenlabs_client.validate_voice(GOOD, client=http)
    assert http.calls == 1
    elevenlabs_client._voice_check_cache.clear()  # the 200 above is cached for this voice id
    flaky = _Http(503)
    await elevenlabs_client.validate_voice(GOOD, client=flaky)
    elevenlabs_client._voice_check_cache.clear()
    await elevenlabs_client.validate_voice(GOOD, client=flaky)
    await elevenlabs_client.validate_voice(GOOD, client=flaky)
    assert flaky.calls == 3  # "could not tell" is asked again every time


async def test_a_malformed_voice_id_is_rejected_without_a_request() -> None:
    http = _Http(200)
    assert await elevenlabs_client.validate_voice("../x?y=1", client=http) == VoiceCheck(False, "malformed_voice_id")
    assert http.calls == 0


async def test_the_preflight_message_never_names_the_voice_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async def rejected(voice_id: str, **_: Any) -> VoiceCheck:
        return VoiceCheck(False, "voice_not_found")

    monkeypatch.setattr(elevenlabs_client, "validate_voice", rejected)
    message = await elevenlabs_client.preflight_error(None)
    assert message and "voice_not_found" in message and "no call was placed" in message and GOOD not in message


# --- the dashboard dial, refused early and without writing anything ----------------------------------------------------


@pytest.fixture
async def dialable(client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from apps.api.routers import admin as admin_module

    await _seed_org(db_session)
    db_session.add(OrgPhoneNumber(org_id=ORG_ID, provider="plivo", phone_number="15550001111", is_default=True))
    await db_session.commit()
    monkeypatch.setattr(admin_module.voice_failover, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(admin_module, "start_precall_connect", lambda *a, **k: None)
    monkeypatch.setattr(settings, "environment", "dev")
    seen = {"dialled": 0}

    async def fake_initiate_call(*args: Any, **kwargs: Any) -> tuple[dict, str]:
        seen["dialled"] += 1
        return {"request_uuid": "req-1"}, "plivo"

    monkeypatch.setattr(admin_module.voice_failover, "initiate_call", fake_initiate_call)
    return seen


async def _users(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(User))).scalar_one()


async def test_a_number_off_the_allowlist_is_refused_before_any_record_is_written(
    client: AsyncClient, db_session: AsyncSession, dialable: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_dial_allowlist", TESTER)
    before = await _users(db_session)
    response = await client.post("/admin/outbound/call", json={"to_phone": STRANGER}, headers=ADMIN_HEADERS)
    assert response.status_code == 403 and "allowlist" in response.json()["detail"]
    assert dialable["dialled"] == 0 and await _users(db_session) == before


async def test_the_listed_number_is_dialled(
    client: AsyncClient, dialable: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_dial_allowlist", TESTER)
    response = await client.post("/admin/outbound/call", json={"to_phone": TESTER}, headers=ADMIN_HEADERS)
    assert response.status_code == 200 and dialable["dialled"] == 1


async def test_a_rejected_voice_refuses_the_call_before_any_record_is_written(
    client: AsyncClient, db_session: AsyncSession, dialable: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_preflight_voice_check", True)
    monkeypatch.setattr(settings, "voice_tts_provider", "elevenlabs")

    async def rejected(voice_id: str, **_: Any) -> VoiceCheck:
        return VoiceCheck(False, "voice_not_found")

    monkeypatch.setattr(elevenlabs_client, "validate_voice", rejected)
    before = await _users(db_session)
    response = await client.post("/admin/outbound/call", json={"to_phone": TESTER}, headers=ADMIN_HEADERS)
    assert response.status_code == 503 and "no call was placed" in response.json()["detail"]
    assert dialable["dialled"] == 0 and await _users(db_session) == before


@pytest.mark.parametrize("verdict", [VoiceCheck(True, "ok"), VoiceCheck(None, "unreachable")])
async def test_a_valid_or_unverifiable_voice_never_blocks_the_call(
    client: AsyncClient, dialable: dict[str, Any], monkeypatch: pytest.MonkeyPatch, verdict: VoiceCheck
) -> None:
    monkeypatch.setattr(settings, "voice_preflight_voice_check", True)
    monkeypatch.setattr(settings, "voice_tts_provider", "elevenlabs")

    async def answer(voice_id: str, **_: Any) -> VoiceCheck:
        return verdict

    monkeypatch.setattr(elevenlabs_client, "validate_voice", answer)
    response = await client.post("/admin/outbound/call", json={"to_phone": TESTER}, headers=ADMIN_HEADERS)
    assert response.status_code == 200 and dialable["dialled"] == 1


async def test_with_the_check_off_a_bad_voice_does_not_stop_the_call(
    client: AsyncClient, dialable: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_preflight_voice_check", False)

    async def must_not_run(*a: Any, **k: Any) -> VoiceCheck:
        raise AssertionError("the provider was asked although the check is off")

    monkeypatch.setattr(elevenlabs_client, "validate_voice", must_not_run)
    response = await client.post("/admin/outbound/call", json={"to_phone": TESTER}, headers=ADMIN_HEADERS)
    assert response.status_code == 200


# --- a failed synthesis is an error, never a spoken turn ------------------------------------------------------------------

REJECTION = {"message": f"A voice with voice_id {GOOD} does not exist.", "error": "voice_id_does_not_exist", "code": 1008}


async def test_a_failed_turn_is_logged_as_an_error_and_not_stored_as_spoken(
    real_tts: dict[str, Any], fakes: list[tuple[str, Any]]
) -> None:
    real_tts["ws"] = _Ws([REJECTION])
    call = Call()
    with capture_logs() as captured:
        await _agent_turn(call, structlog.get_logger())
    errors = [e for e in captured if e.get("event") == "voice_tts_failed"]
    assert len(errors) == 1 and errors[0]["log_level"] == "error"
    assert errors[0]["code"] == "voice_id_does_not_exist" and errors[0]["spoken"] is False and errors[0]["partial"] is False
    assert fakes == []  # nothing was stored as the agent's words
    assert call.state.tts_failed_turns == 1 and call.state.memory is None
    assert SPOKEN not in json.dumps(errors, default=str) and GOOD not in json.dumps(errors, default=str)


async def test_a_healthy_turn_is_still_stored_and_raises_no_error(
    real_tts: dict[str, Any], fakes: list[tuple[str, Any]]
) -> None:
    real_tts["ws"] = _Ws([{"audio": "QUJD"}, {"audio": "REVG", "isFinal": True}])
    call = Call()
    with capture_logs() as captured:
        await _agent_turn(call, structlog.get_logger())
    assert [text for text, _ in fakes] == [SPOKEN] and call.state.tts_failed_turns == 0
    assert not [e for e in captured if e.get("event") == "voice_tts_failed"]


async def test_a_closed_connection_is_also_an_error(real_tts: dict[str, Any], fakes: list[tuple[str, Any]]) -> None:
    real_tts["ws"] = _Ws([], refuse_sends=True)
    call = Call()
    with capture_logs():
        await _agent_turn(call, structlog.get_logger())
    assert fakes == []  # whichever way the turn fails, it is never stored as spoken


# --- diagnostics: every environment but production ----------------------------------------------------------------------


@pytest.mark.parametrize("environment, flag, expected", [("dev", True, True), ("pilot", True, True), ("production", True, False), ("dev", False, False), ("pilot", False, False)])
def test_the_diagnostics_run_everywhere_except_production(monkeypatch: pytest.MonkeyPatch, environment: str, flag: bool, expected: bool) -> None:
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "voice_pipeline_diag", flag)
    assert adapter.diag_enabled() is expected


# --- the audio format and delivery path to the phone -----------------------------------------------------------------------


async def test_audio_is_requested_as_8khz_mulaw_and_delivered_untouched_in_plivos_envelope(
    real_tts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    urls: list[str] = []
    ws = _Ws([{"audio": base64.b64encode(bytes(range(160))).decode()}, {"audio": base64.b64encode(bytes(range(160))).decode(), "isFinal": True}])

    async def connect(url: str) -> _Ws:
        urls.append(url)
        return ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", connect)
    call = Call()
    await _agent_turn(call, structlog.get_logger())
    assert "output_format=ulaw_8000" in urls[0]  # mu-law at 8 kHz straight from the provider: no resampling anywhere
    frames = [m for m in call.phone.sent if m.get("event") == "playAudio"]
    assert len(frames) == 2
    for frame in frames:
        assert frame["media"]["contentType"] == "audio/x-mulaw" and frame["media"]["sampleRate"] == 8000
        assert base64.b64decode(frame["media"]["payload"]) == bytes(range(160))  # the provider's bytes, byte for byte
    assert call.state.playback_end > 0  # the playback horizon follows the bytes sent (160 bytes = 20 ms each)


def test_twilios_envelope_carries_the_stream_sid() -> None:
    assert adapter._media_message("twilio", "MZ123", "QUJD") == {"event": "media", "streamSid": "MZ123", "media": {"payload": "QUJD"}}


# --- the configuration diagnostic ----------------------------------------------------------------------------------------


def _codes(**s: Any) -> set[str]:
    base = {"voice_tts_provider": "elevenlabs", "elevenlabs_api_key": "k", "openai_api_key": "k", "plivo_auth_token": "t", "public_base_url": "https://x.example.org"}
    return {i.code for i in config_check.check_voice_config(SimpleNamespace(**{**base, **s}))}


def test_a_development_stack_with_provider_credentials_and_no_allowlist_is_warned() -> None:
    assert "dev_dial_unrestricted" in _codes(environment="dev", voice_dial_allowlist="")
    assert "dev_dial_unrestricted" not in _codes(environment="production", voice_dial_allowlist="")
    assert "dial_allowlist_active" in _codes(environment="dev", voice_dial_allowlist="+919876543210")
    assert "dev_dial_unrestricted" not in _codes(environment="dev", voice_dial_allowlist="+919876543210")


def test_the_new_flags_are_visible_to_the_voice_config_diagnostic() -> None:
    assert {"voice_pipeline_diag", "voice_preflight_voice_check"} <= set(config_check.BEHAVIOUR_FLAGS)
