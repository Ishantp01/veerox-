"""Phase 5: recovery when ElevenLabs fails mid-call (channels/voice/recovery.py and its
hooks in the adapter), driven through the real adapter with a scriptable fake session.

The invariant these tests protect: whatever ElevenLabs does, the caller is never left on
an open, silent line. Every failure path ends in recovered speech, or an apology plus a
controlled hangup, within a bounded time. Nothing touches a network or a paid API.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from apps.api.channels.voice import adapter, call_control, elevenlabs_client, recovery
from apps.api.channels.voice.voice_profile import resolve_profile
from apps.api.config import settings
from apps.api.tests.test_elevenlabs_client import _FakeElevenLabsWS

ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER = uuid.UUID("00000000-0000-0000-0000-000000000ccc")
_RealSession = elevenlabs_client.ElevenLabsTTSSession


class RSession:
    """A scriptable stand-in for ElevenLabsTTSSession. ``RSession.script[i]`` describes the i-th
    session a call opens:

    * ``open_error``  - raising this from __aenter__
    * ``audio``       - chunks produced once text arrives
    * ``fail``        - after those chunks, end the stream with this failure reason (no final marker)
    * ``silent``      - never produce audio
    * ``final_on_finish`` - finish() ends the stream cleanly even though no audio came (the silent-turn case)
    """

    instances: list["RSession"] = []
    script: list[dict[str, Any]] = []

    def __init__(self, voice_id: str | None = None, model_id: str | None = None, profile: Any = None) -> None:
        self.index = len(RSession.instances)
        self.behaviour = RSession.script[self.index] if self.index < len(RSession.script) else {"audio": ["QUJD"]}
        self.profile = profile
        self.sent: list[str] = []
        self.finished = False
        self.closed = False
        self.connect_ms = 1
        self.failed = False
        self.fail_reason: str | None = None
        self.final_seen = False
        self.audio_chunks_received = 0
        self.chars_sent = 0
        self.on_close: Any = None
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._delivered = False
        RSession.instances.append(self)

    async def __aenter__(self) -> "RSession":
        error = self.behaviour.get("open_error")
        if error is not None:
            raise error
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.closed = True
        if self.on_close is not None:
            self.on_close(self.chars_sent)

    async def send_text(self, text: str) -> None:
        if self.behaviour.get("send_error") is not None:
            raise self.behaviour["send_error"]
        self.sent.append(text)
        self.chars_sent += len(text)
        if self._delivered or self.behaviour.get("silent"):
            return
        self._delivered = True
        for chunk in self.behaviour.get("audio", []):
            self.audio_chunks_received += 1
            self._queue.put_nowait(chunk)
        reason = self.behaviour.get("fail")
        if reason:
            self.failed, self.fail_reason = True, reason
            self._queue.put_nowait(None)

    async def finish(self) -> None:
        self.finished = True
        if not self.failed and (not self.behaviour.get("silent") or self.behaviour.get("final_on_finish")):
            self.final_seen = True
            self._queue.put_nowait(None)

    async def audio_chunks(self) -> Any:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            yield chunk


class _Phone:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self) -> None:
        return None


class _Agent:
    async def send(self, message: str) -> None:
        return None


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def info(self, event: str, *a: Any, **k: Any) -> None:
        self.events.append(event)

    warning = info


class Call:
    def __init__(self, **fields: Any) -> None:
        self.state = adapter.CallState(
            user_id=USER, conversation_id=uuid.uuid4(), org_id=ORG, base_instructions="BASE",
            tts_provider="elevenlabs", call_uuid="call-1",
        )
        self.phone, self.agent, self.log = _Phone(), _Agent(), _Log()
        self.state.call_ws = self.phone
        for key, value in fields.items():
            setattr(self.state, key, value)

    async def event(self, etype: str, **fields: Any) -> None:
        await adapter.handle_openai_event({"type": etype, **fields}, self.phone, self.agent, self.state, self.log)  # type: ignore[arg-type]

    def audio(self) -> list[str]:
        return [m["media"]["payload"] for m in self.phone.sent if m.get("event") == "playAudio"]

    def cleared(self) -> bool:
        return any(m.get("event") == "clearAudio" for m in self.phone.sent)

    async def settle(self, seconds: float = 0.2) -> None:
        for _ in range(60):
            task = self.state.tts_recovery_task
            if task is None or task.done():
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(seconds)


@pytest.fixture(autouse=True)
def world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    RSession.instances = []
    RSession.script = []
    recovery.fallback_audio.clear()
    monkeypatch.setattr(elevenlabs_client, "ElevenLabsTTSSession", RSession)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    for name, value in (
        ("voice_tts_recovery", True),
        ("voice_elevenlabs_prewarm", False),
        ("voice_nonblocking_tts_finish", False),
        ("voice_end_call_grace_seconds", 0.0),
        ("voice_end_call_max_wait_seconds", 1.0),
        ("voice_tts_connect_timeout_seconds", 1.0),
        ("voice_tts_first_audio_timeout_seconds", 30.0),
        ("voice_tts_max_failures_per_call", 2),
    ):
        monkeypatch.setattr(settings, name, value)

    seen = SimpleNamespace(hangups=[], outcomes=[], tools=[], persisted=[])

    async def hangup(state: Any, log: Any) -> str:
        seen.hangups.append(state.call_uuid)
        return "ok"

    async def outcome(conversation_id: Any, org_id: Any, name: str) -> None:
        seen.outcomes.append(name)

    async def tool(name: str, args_json: str, *a: Any, **k: Any) -> dict[str, Any]:
        seen.tools.append(name)
        return {"status": "ok"}

    async def persist(state: Any, text: str, *, pending: Any = None) -> None:
        seen.persisted.append(text)

    async def no_synth(text: str, voice: Any = None) -> list[str]:
        return []  # the background apology fill must not consume the scripted sessions

    monkeypatch.setattr(recovery, "synthesize_greeting", no_synth)
    monkeypatch.setattr(call_control, "provider_hangup", hangup)
    monkeypatch.setattr(call_control, "record_call_outcome", outcome)
    monkeypatch.setattr(adapter, "_dispatch_realtime_tool", tool)
    monkeypatch.setattr(adapter, "_persist_voice_turn", persist)
    yield seen
    recovery.fallback_audio.clear()


def _cache_apology(voice: Any = None, chunks: tuple[str, ...] = ("QVBP", "TE9H")) -> None:
    recovery.fallback_audio._entries[recovery.FallbackAudioCache.key(voice)] = chunks


async def _until(condition: Any, seconds: float = 4.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not condition():
        await asyncio.sleep(0.05)


async def _first_delta(call: Call, text: str = "Namaste") -> None:
    await call.event("response.created", response={"id": "r1"})
    await call.event("response.text.delta", delta=text, response_id="r1")


# --- recovered speech ---------------------------------------------------------------------------------------------------------


async def test_a_failed_connect_is_retried_once_and_the_turn_is_still_spoken(world: SimpleNamespace) -> None:
    RSession.script = [{"open_error": OSError("connection refused")}, {"audio": ["QQ=="]}]
    call = Call()
    await _first_delta(call, "Namaste")
    await call.settle()

    assert len(RSession.instances) == 2 and RSession.instances[1].sent == ["Namaste"]  # the text was replayed
    assert call.audio() == ["QQ=="] and not call.state.tts_failed_over and call.state.tts_failures == 1
    assert world.hangups == [] and world.tools == []  # an ordinary blip does not end the call
    await call.event("response.text.delta", delta=" ji", response_id="r1")  # the rest of the turn continues
    assert RSession.instances[1].sent == ["Namaste", " ji"]
    await adapter.cancel_pending_tasks(call.state)


async def test_text_that_arrives_while_recovering_is_not_lost(world: SimpleNamespace) -> None:
    RSession.script = [{"open_error": OSError("x")}, {"audio": ["QQ=="]}]
    call = Call()
    await call.event("response.created", response={"id": "r1"})
    await call.event("response.text.delta", delta="Pehla ", response_id="r1")
    await call.event("response.text.delta", delta="aur doosra", response_id="r1")  # lands during recovery
    await call.settle()
    assert "".join(RSession.instances[-1].sent) == "Pehla aur doosra"
    await adapter.cancel_pending_tasks(call.state)


async def test_a_dropped_stream_is_recovered_and_the_audio_already_played_stays_played(world: SimpleNamespace) -> None:
    RSession.script = [{"audio": ["AA=="], "fail": "connection_closed:1011"}, {"audio": ["BB=="]}]
    call = Call()
    await _first_delta(call, "Dava India franchise")
    await call.settle(0.3)
    assert call.audio()[0] == "AA==" and "BB==" in call.audio()
    assert RSession.instances[1].sent == ["Dava India franchise"] and call.state.tts_failures == 1
    await adapter.cancel_pending_tasks(call.state)


async def test_a_provider_that_rejects_the_voice_settings_is_retried_with_safe_ones(world: SimpleNamespace) -> None:
    voice = resolve_profile({"voice_id": "abcdefghij1234", "model_id": "eleven_flash_v2_5"})
    assert voice is not None
    RSession.script = [{"fail": "invalid voice_settings: speed out of range"}, {"audio": ["QQ=="]}]
    call = Call(voice=voice)
    await _first_delta(call)
    await call.settle()
    assert RSession.instances[0].profile is voice and RSession.instances[1].profile is None  # platform defaults
    assert call.audio() == ["QQ=="] and not call.state.tts_failed_over


async def test_a_stalled_provider_is_noticed_by_the_watchdog(world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_tts_first_audio_timeout_seconds", 1.0)  # the clamp's minimum
    RSession.script = [{"silent": True}, {"audio": ["QQ=="]}]
    call = Call()
    await _first_delta(call)
    await asyncio.sleep(0.5)
    assert call.audio() == []  # not yet: it is still within the allowed time
    await asyncio.sleep(1.0)
    await call.settle()
    assert call.audio() == ["QQ=="] and call.state.tts_failures == 1


async def test_a_turn_that_ended_without_any_audio_is_replayed(world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_nonblocking_tts_finish", True)
    RSession.script = [{"silent": True, "final_on_finish": True}, {"audio": ["QQ=="]}]
    call = Call()
    await _first_delta(call, "Dhanyavaad")
    await call.event("response.text.done", text="Dhanyavaad", response_id="r1")
    await asyncio.sleep(0.3)
    await call.settle()
    await asyncio.sleep(0.2)
    assert call.audio() == ["QQ=="] and RSession.instances[1].sent == ["Dhanyavaad"]
    assert world.persisted == ["Dhanyavaad"]  # recorded once, by the successful replay
    await adapter.cancel_pending_tasks(call.state)


async def test_punctuation_only_text_is_not_mistaken_for_a_silent_failure(world: SimpleNamespace) -> None:
    RSession.script = [{"silent": True, "final_on_finish": True}]
    call = Call()
    await _first_delta(call, "...")
    await call.event("response.text.done", text="...", response_id="r1")
    await call.settle(0.2)
    assert call.state.tts_failures == 0 and len(RSession.instances) == 1


# --- out of retries: apology and a controlled end -------------------------------------------------------------------------------


async def test_when_the_retry_fails_too_the_apology_plays_and_the_call_is_ended(world: SimpleNamespace) -> None:
    _cache_apology()
    RSession.script = [{"open_error": OSError("a")}, {"open_error": OSError("b")}]
    call = Call()
    await _first_delta(call)
    await call.settle(0.2)
    await _until(lambda: bool(world.hangups))

    assert call.cleared() and call.audio() == ["QVBP", "TE9H"]  # the pre-generated apology, nothing else
    assert call.state.tts_failed_over
    assert world.tools == ["transfer_to_human"]  # a person is asked to call the customer back
    assert world.hangups == ["call-1"] and world.outcomes == ["tts_failure"]  # and the line is closed


async def test_without_a_cached_apology_the_call_is_still_ended_not_left_silent(world: SimpleNamespace) -> None:
    RSession.script = [{"open_error": OSError("a")}, {"open_error": OSError("b")}]
    call = Call()
    await _first_delta(call)
    await call.settle(0.2)
    await _until(lambda: bool(world.hangups))
    assert call.audio() == [] and world.hangups == ["call-1"] and world.outcomes == ["tts_failure"]


async def test_after_failing_over_nothing_more_is_spoken_or_reopened(world: SimpleNamespace) -> None:
    RSession.script = [{"open_error": OSError("a")}, {"open_error": OSError("b")}]
    call = Call()
    await _first_delta(call)
    await call.settle(0.4)
    count = len(RSession.instances)
    await call.event("response.text.delta", delta="aur kuch", response_id="r1")
    await call.event("response.created", response={"id": "r2"})
    await call.event("response.text.delta", delta="naya jawab", response_id="r2")
    assert len(RSession.instances) == count  # no new sockets; the model's later words go nowhere
    await asyncio.sleep(0.6)


async def test_the_call_wide_failure_limit_skips_straight_to_the_fallback(world: SimpleNamespace) -> None:
    RSession.script = [{"open_error": OSError("a")}, {"audio": ["QQ=="]}]
    call = Call(tts_failures=2)  # two turns have already failed on this call
    await _first_delta(call)
    await call.settle(0.4)
    assert len(RSession.instances) == 1 and call.state.tts_failed_over  # no further retry
    await asyncio.sleep(0.6)


async def test_a_turn_is_retried_only_once(world: SimpleNamespace) -> None:
    RSession.script = [{"fail": "x"}, {"fail": "y"}, {"audio": ["QQ=="]}]
    call = Call()
    await _first_delta(call)
    await call.settle(0.5)
    assert len(RSession.instances) == 2 and call.state.tts_failed_over  # the third script entry is never used
    await asyncio.sleep(0.6)


FAILURES = [
    pytest.param([{"open_error": OSError("a")}, {"open_error": OSError("b")}], id="never_connects"),
    pytest.param([{"fail": "boom"}, {"fail": "boom"}], id="keeps_dropping"),
    pytest.param([{"send_error": OSError("closed")}, {"send_error": OSError("closed")}], id="send_fails"),
]


@pytest.mark.parametrize("script", FAILURES)
async def test_every_failure_path_ends_with_a_bounded_hangup_never_silence(world: SimpleNamespace, script: list[dict[str, Any]]) -> None:
    RSession.script = script
    call = Call()
    started = time.monotonic()
    await _first_delta(call)
    for _ in range(80):
        if world.hangups:
            break
        await asyncio.sleep(0.05)
    assert world.hangups == ["call-1"] and world.outcomes == ["tts_failure"]
    assert time.monotonic() - started < 6.0


# --- not for everyone / not when it should not run --------------------------------------------------------------------------------


async def test_with_recovery_off_a_connect_failure_propagates_exactly_as_before(world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_tts_recovery", False)
    RSession.script = [{"open_error": OSError("refused")}]
    call = Call()
    await call.event("response.created", response={"id": "r1"})
    with pytest.raises(OSError):
        await call.event("response.text.delta", delta="Namaste", response_id="r1")
    assert call.state.tts_recovery_task is None and world.hangups == []


async def test_a_turn_the_caller_cut_off_is_not_resurrected_by_recovery(world: SimpleNamespace) -> None:
    RSession.script = [{"silent": True}, {"audio": ["BB=="]}]
    call = Call()
    await _first_delta(call)
    await asyncio.sleep(0.05)  # the forwarder is now waiting for audio
    session = RSession.instances[0]
    call.state.generation += 1  # the caller interrupted: the old turn must not come back
    session.failed, session.fail_reason = True, "connection_closed:1011"
    session._queue.put_nowait(None)  # ...and the old stream dies afterwards
    await call.settle(0.3)
    assert len(RSession.instances) == 1 and call.state.tts_failures == 0
    await adapter.cancel_pending_tasks(call.state)


async def test_recovery_state_is_per_call(world: SimpleNamespace) -> None:
    RSession.script = [{"open_error": OSError("a")}, {"open_error": OSError("b")}]
    broken, healthy = Call(), Call()
    await _first_delta(broken)
    await broken.settle(0.4)
    assert broken.state.tts_failed_over and not healthy.state.tts_failed_over
    assert healthy.state.tts_failures == 0
    await asyncio.sleep(0.6)


# --- the apology audio cache ------------------------------------------------------------------------------------------------------------


async def test_the_apology_is_made_while_the_provider_works_and_only_when_enabled(world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[Any] = []

    async def synth(text: str, voice: Any = None) -> list[str]:
        made.append((text, voice))
        return ["QVBP"]

    monkeypatch.setattr(recovery, "synthesize_greeting", synth)
    monkeypatch.setattr(settings, "voice_tts_recovery", False)
    recovery.fallback_audio.ensure_in_background(None)
    await asyncio.sleep(0.05)
    assert made == [] and recovery.fallback_audio.get(None) is None  # off: nothing is made

    monkeypatch.setattr(settings, "voice_tts_recovery", True)
    recovery.fallback_audio.ensure_in_background(None)
    recovery.fallback_audio.ensure_in_background(None)  # in flight: not duplicated
    await asyncio.sleep(0.05)
    assert made == [(recovery.APOLOGY, None)] and recovery.fallback_audio.get(None) == ("QVBP",)


async def test_each_voice_gets_its_own_apology_and_never_another_orgs(world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    async def synth(text: str, voice: Any = None) -> list[str]:
        return [f"audio-for-{getattr(voice, 'voice_id', 'platform')}"]

    monkeypatch.setattr(recovery, "synthesize_greeting", synth)
    voice_a = resolve_profile({"voice_id": "aaaaaaaaaa1111", "model_id": "eleven_flash_v2_5"})
    voice_b = resolve_profile({"voice_id": "bbbbbbbbbb2222", "model_id": "eleven_turbo_v2_5"})
    for voice in (None, voice_a, voice_b):
        recovery.fallback_audio.ensure_in_background(voice)
    await asyncio.sleep(0.1)
    assert recovery.fallback_audio.get(voice_a) == ("audio-for-aaaaaaaaaa1111",)
    assert recovery.fallback_audio.get(voice_b) == ("audio-for-bbbbbbbbbb2222",)
    assert recovery.fallback_audio.get(None) == ("audio-for-platform",)
    assert recovery.FallbackAudioCache.key(voice_a) != recovery.FallbackAudioCache.key(voice_b)


async def test_a_failed_apology_synthesis_caches_nothing(world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    async def synth(text: str, voice: Any = None) -> list[str]:
        return []

    monkeypatch.setattr(recovery, "synthesize_greeting", synth)
    recovery.fallback_audio.ensure_in_background(None)
    await asyncio.sleep(0.05)
    assert recovery.fallback_audio.get(None) is None


# --- the real client reports failures it used to swallow ------------------------------------------------------------------------------------


async def _real_session(monkeypatch: pytest.MonkeyPatch, messages: list[dict[str, Any]]) -> Any:
    ws = _FakeElevenLabsWS(server_messages=messages)

    async def connect(url: str) -> _FakeElevenLabsWS:
        return ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", connect)
    return await _RealSession().__aenter__()


async def test_a_provider_error_message_marks_the_session_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    session = await _real_session(monkeypatch, [{"error": "invalid_voice_settings", "message": "speed out of range"}])
    await asyncio.sleep(0.05)
    assert session.failed and "invalid_voice_settings" in (session.fail_reason or "") and not session.final_seen
    assert [c async for c in session.audio_chunks()] == []
    await session.__aexit__(None, None, None)


async def test_a_normal_turn_is_not_flagged_and_counts_its_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    session = await _real_session(monkeypatch, [{"audio": "QUJD", "isFinal": False}, {"audio": "REVG", "isFinal": True}])
    reported: list[int] = []
    session.on_close = reported.append
    await session.send_text("Namaste ji")
    await session.finish()
    assert [c async for c in session.audio_chunks()] == ["QUJD", "REVG"]
    assert session.final_seen and not session.failed and session.audio_chunks_received == 2
    assert session.chars_sent == len("Namaste ji")
    await session.__aexit__(None, None, None)
    await session.__aexit__(None, None, None)  # closing twice reports once
    assert reported == [len("Namaste ji")]
