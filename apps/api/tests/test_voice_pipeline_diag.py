# ruff: noqa: E501, F811  (long literals; the autouse fixture is imported by name)
"""The silent-agent failure (a call that connects, hears the caller and generates replies, but never speaks) and the
pilot-only diagnostics (VOICE_PIPELINE_DIAG) that name the failing stage from counts alone.

The replay uses the REAL ElevenLabsTTSSession against a fake WebSocket that behaves like a provider rejecting the voice,
driven through the real event handler: no network, no call. The shape of the provider's error message is assumed from
the code path (the reader treats any message carrying ``error`` or ``message`` as a failure).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest
import structlog
import websockets
from structlog.testing import capture_logs

from apps.api.channels.voice import adapter, elevenlabs_client
from apps.api.channels.voice.elevenlabs_client import ElevenLabsTTSSession as RealSession
from apps.api.config import Settings, settings
from apps.api.tests.test_voice_tts_turns import Call, fakes  # noqa: F401  (autouse fixture)

SPOKEN = "Namaste, main Veerox ka AI assistant hoon. Kya main aapse baat kar sakta hoon?"
BAD_VOICE = "Be3X8pg7kLN4vyyMC3QN"


class _Ws:
    """A fake provider socket: replays server messages, optionally refuses sends like a closed connection."""

    def __init__(self, messages: list[dict[str, Any]], *, refuse_sends: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self._messages, self._refuse = messages, refuse_sends

    async def send(self, raw: str) -> None:
        if self._refuse:
            raise websockets.exceptions.ConnectionClosedError(None, None)
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        return None

    def __aiter__(self) -> _Ws:
        self._it = iter(self._messages)
        return self

    async def __anext__(self) -> str:
        try:
            return json.dumps(next(self._it))
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.fixture
def real_tts(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The real session class, talking to whatever fake socket the test installs."""
    holder: dict[str, Any] = {}
    monkeypatch.setattr(elevenlabs_client, "ElevenLabsTTSSession", RealSession)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")

    async def connect(url: str) -> _Ws:
        return holder["ws"]

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", connect)
    return holder


@pytest.fixture
def diag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "pilot")
    monkeypatch.setattr(settings, "voice_pipeline_diag", True)


async def _agent_turn(call: Call, log: Any, text: str = SPOKEN) -> None:
    async def event(etype: str, **fields: Any) -> None:
        await adapter.handle_openai_event({"type": etype, **fields}, call.phone, call.agent, call.state, log)  # type: ignore[arg-type]

    await event("response.created")
    with contextlib.suppress(Exception):  # the bridge's event pump swallows handler errors the same way
        await event("response.text.delta", delta=text)
    await asyncio.sleep(0.05)
    await event("response.text.done", text=text)


def _events(captured: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in captured if e.get("event") == name]


# --- the provider's error code ------------------------------------------------------------------------------


def test_error_code_is_a_short_token_and_never_the_message() -> None:
    assert elevenlabs_client.error_code({"message": f"A voice with voice_id {BAD_VOICE} does not exist.", "error": "voice_id_does_not_exist", "code": 1008}) == "voice_id_does_not_exist"
    assert elevenlabs_client.error_code({"detail": {"status": "quota_exceeded"}}) == "quota_exceeded"
    assert elevenlabs_client.error_code({"message": f"A voice with voice_id {BAD_VOICE} does not exist."}) == "provider_error"
    assert elevenlabs_client.error_code({"error": "Has Spaces And Text"}) == "provider_error"


# --- the symptom, reproduced -----------------------------------------------------------------------------------


async def test_a_rejected_voice_gives_a_silent_turn_that_is_no_longer_saved_as_spoken(
    real_tts: dict[str, Any], fakes: list[tuple[str, Any]]  # noqa: F811
) -> None:
    """The dev call: text is generated, the provider refuses the voice, no audio ever reaches the line. It used to be
    stored (and logged) as if said; now it is reported as a failed turn and not stored."""
    real_tts["ws"] = _Ws([{"message": f"A voice with voice_id {BAD_VOICE} does not exist.", "error": "voice_id_does_not_exist", "code": 1008}])
    call = Call()
    await _agent_turn(call, structlog.get_logger())
    assert call.audio_sent() == []  # nothing reached Plivo
    assert fakes == []  # and it is NOT stored as something the agent said
    assert call.state.eleven_turn_audio_chunks == 0


async def test_with_the_diagnostics_on_the_silent_turn_is_named_without_any_content(
    real_tts: dict[str, Any], diag_on: None
) -> None:
    real_tts["ws"] = _Ws([{"message": f"A voice with voice_id {BAD_VOICE} does not exist.", "error": "voice_id_does_not_exist", "code": 1008}])
    call = Call()
    call.state.diag_on = adapter.diag_enabled()
    with capture_logs() as captured:
        await _agent_turn(call, structlog.get_logger())
        adapter.diag_call_summary(call.state, structlog.get_logger())
    turn = _events(captured, "voice_pipeline_turn")[0]
    assert turn["silent_turn"] is True and turn["tts_failed"] is True and turn["tts_error_code"] == "voice_id_does_not_exist"
    assert turn["text_chars"] == len(SPOKEN) and turn["tts_chunks"] == 0
    summary = _events(captured, "voice_pipeline_call_summary")[0]
    assert summary["silent_turns"] == 1 and summary["tts_failures"] == 1
    for line in (turn, summary):  # no speech, text or voice id anywhere in what was logged
        blob = json.dumps(line, default=str)
        assert SPOKEN not in blob and BAD_VOICE not in blob and "Namaste" not in blob


async def test_a_socket_closed_during_the_turn_is_also_named(real_tts: dict[str, Any], diag_on: None) -> None:
    real_tts["ws"] = _Ws([], refuse_sends=True)  # the provider has already closed the connection
    call = Call()
    call.state.diag_on = adapter.diag_enabled()
    with capture_logs() as captured:
        await _agent_turn(call, structlog.get_logger())
    turn = _events(captured, "voice_pipeline_turn")[0]
    assert turn["tts_failed"] is True and turn["silent_turn"] is True
    assert turn["tts_error_code"] in ("no_session", "closed_during_send")  # the session never opened for this turn


async def test_a_healthy_turn_is_not_reported_silent(real_tts: dict[str, Any], diag_on: None) -> None:
    real_tts["ws"] = _Ws([{"audio": "QUJD"}, {"audio": "REVG", "isFinal": True}])
    call = Call()
    call.state.diag_on = adapter.diag_enabled()
    call.state.diag_in_frames = 300  # the caller's audio was flowing
    with capture_logs() as captured:
        await _agent_turn(call, structlog.get_logger())
        adapter.diag_call_summary(call.state, structlog.get_logger())
    turn = _events(captured, "voice_pipeline_turn")[0]
    assert turn["silent_turn"] is False and turn["tts_failed"] is False and turn["tts_chunks"] == 2
    assert call.audio_sent() == ["QUJD", "REVG"]
    assert _events(captured, "voice_pipeline_call_summary")[0]["first_failing_stage"] == "none"


# --- gating: off by default, pilot only ---------------------------------------------------------------------------


def test_the_flag_is_off_by_default() -> None:
    assert Settings.model_fields["voice_pipeline_diag"].default is False


async def test_nothing_is_logged_when_the_flag_is_off_or_in_production(
    real_tts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for environment, flag in (("pilot", False), ("dev", False), ("production", True)):
        monkeypatch.setattr(settings, "environment", environment)
        monkeypatch.setattr(settings, "voice_pipeline_diag", flag)
        assert adapter.diag_enabled() is False
        real_tts["ws"] = _Ws([{"error": "voice_id_does_not_exist"}])
        call = Call()
        call.state.diag_on = adapter.diag_enabled()
        with capture_logs() as captured:
            await _agent_turn(call, structlog.get_logger())
            adapter.diag_call_summary(call.state, structlog.get_logger())
        assert not [e for e in captured if str(e.get("event", "")).startswith("voice_pipeline")], (environment, flag)


# --- counters and the first-failing-stage rule ----------------------------------------------------------------------


async def test_frames_are_counted_in_and_out_when_on_and_not_when_off(diag_on: None) -> None:
    class Phone:
        async def send_text(self, message: str) -> None:
            return None

    for on in (True, False):
        call = Call()
        call.state.diag_on = on
        await adapter._send_media(Phone(), call.state, "QUJDREVG")  # type: ignore[arg-type]  # 6 bytes
        assert call.state.diag_out_frames == (1 if on else 0) and call.state.diag_out_bytes == (6 if on else 0)


@pytest.mark.parametrize(
    "frames_in, turns, silent, expected",
    [(0, 0, 0, "inbound_audio"), (500, 0, 0, "no_agent_turn"), (500, 3, 3, "tts"), (500, 3, 0, "none")],
)
def test_the_first_failing_stage_follows_from_the_counts(frames_in: int, turns: int, silent: int, expected: str) -> None:
    call = Call()
    st = call.state
    st.diag_on, st.diag_in_frames, st.diag_turns, st.diag_silent_turns = True, frames_in, turns, silent
    st.diag_first_failure = "tts" if silent else None
    with capture_logs() as captured:
        adapter.diag_call_summary(st, structlog.get_logger())
    assert _events(captured, "voice_pipeline_call_summary")[0]["first_failing_stage"] == expected
