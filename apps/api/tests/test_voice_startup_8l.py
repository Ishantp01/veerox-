"""Phase 8L: getting the greeting to the callee quickly, without weakening anything around it.

The Phase 8K live call took about 19 s from answer to the first greeting audio. The causes found in its log: the pre-connected
OpenAI session expired (20 s) before a call that rang for 24 s was answered, the answer handler waited for two database writes
before returning its markup, the stream start-up ran its database reads one after another, and the greeting was synthesised live
(3.6 s to the first audio chunk on a cold voice). These tests drive the real bridge with fake sockets and fake providers that
have simulated delays. They prove ORDER and OVERLAP (what waits for what); they cannot prove real network timings.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest
from fastapi import BackgroundTasks, Request, WebSocketDisconnect

from apps.api.channels.voice import adapter, compliance, latency, webhook
from apps.api.channels.voice import realtime_bridge as bridge
from apps.api.channels.voice.greeting import CachedGreeting, GreetingAudioCache
from apps.api.config import settings

ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
GREETING = "Namaste, main Veerox ka AI assistant Veer bol raha hoon. Kya main aapse do minute baat kar sakta hoon?"
CHUNKS = ("QUJD" * 40, "REVG" * 40, "R0hJ" * 40)  # synthetic base64 audio; 120 bytes each
CALL = "call-8l-1"
PHONE = "+911234567890"


# --- fakes ----------------------------------------------------------------------------------------------------------------------------------


class CallWS:
    """The provider's media socket: frames it sends us, and what we send back (with times)."""

    def __init__(self, params: dict[str, str] | None = None, *, fail_on_send: int | None = None, disconnect_on_send: bool = False) -> None:
        self.query_params = params or {"from": PHONE, "call_uuid": CALL, "provider": "plivo", "org_id": str(ORG)}
        self.sent: list[tuple[float, dict[str, Any]]] = []
        self.closed: int | None = None
        self._frames = [{"event": "connected"}, {"event": "start", "start": {"streamId": "stream-1"}}]
        self.hangup = asyncio.Event()
        self._fail_on_send, self._disconnect = fail_on_send, disconnect_on_send

    async def accept(self, *a: Any, **k: Any) -> None:
        return None

    async def close(self, code: int = 1000) -> None:
        self.closed = code

    async def receive_text(self) -> str:
        if self._frames:
            return json.dumps(self._frames.pop(0))
        await self.hangup.wait()
        raise WebSocketDisconnect()

    async def send_text(self, message: str) -> None:
        if self._disconnect:
            raise WebSocketDisconnect()
        if self._fail_on_send is not None and len([m for _, m in self.sent if m.get("event") == "playAudio"]) >= self._fail_on_send:
            raise RuntimeError("provider socket broke")
        self.sent.append((time.monotonic(), json.loads(message)))

    def audio(self) -> list[tuple[float, dict[str, Any]]]:
        return [(t, m) for t, m in self.sent if m.get("event") == "playAudio"]


class FakeAI:
    """The OpenAI Realtime socket."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._gone = asyncio.Event()
        self.close_code = None

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self) -> None:
        self.closed = True
        self._gone.set()

    def __aiter__(self) -> "FakeAI":
        return self

    async def __anext__(self) -> str:
        await self._gone.wait()
        raise StopAsyncIteration

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


class World:
    def __init__(self) -> None:
        self.events: list[tuple[str, float]] = []
        self.persisted: list[str] = []
        self.closed_conversations: list[Any] = []
        self.ai = FakeAI()
        self.cache = GreetingAudioCache()
        self.synth_requests: list[str] = []

    def mark(self, name: str) -> None:
        self.events.append((name, time.monotonic()))

    def at(self, name: str) -> float:
        return next(t for n, t in self.events if n == name)

    def order(self) -> list[str]:
        return [n for n, _ in sorted(self.events, key=lambda e: e[1])]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()
    for name, value in (
        ("environment", "dev"), ("plivo_signature_mode", "off"), ("voice_greeting_audio_cache", True), ("voice_tts_provider", "elevenlabs"),
        ("voice_live_turn_detection", False), ("voice_metering_v2", False), ("elevenlabs_api_key", "sk_test"), ("voice_latency_tracking", True),
        ("pilot_allowed_numbers", ""), ("voice_reconnect_recovery", False),
    ):
        monkeypatch.setattr(settings, name, value)
    latency._STARTUPS.clear()

    delays = {"conversation": 0.4, "context": 0.05, "claim": 0.0, "connect": 0.0}
    w.delays = delays  # type: ignore[attr-defined]

    async def open_conversation(caller: str, call_uuid: str | None = None, org_id: Any = None) -> tuple[Any, Any]:
        await asyncio.sleep(delays["conversation"])
        w.mark("conversation_opened")
        return uuid.uuid4(), uuid.uuid4()

    async def close_conversation(conversation_id: Any, campaign_target_id: Any = None) -> None:
        w.closed_conversations.append(conversation_id)

    async def persist(state: Any, text: str, *, pending: Any = adapter._UNSET) -> None:
        w.persisted.append(text)

    async def call_context(org_id: Any, remote: Any, **kw: Any) -> Any:
        await asyncio.sleep(delays["context"])
        return "Veerox", compliance.resolve_recording_policy({"recording_mode": "off"}, remote)

    async def load_greeting(org_id: Any, **kw: Any) -> str:
        await asyncio.sleep(delays["context"])
        return GREETING

    async def load_voice(org_id: Any, **kw: Any) -> None:
        await asyncio.sleep(delays["context"])
        return None

    async def claim(call_uuid: str, expected_org: Any = None) -> Any:
        await asyncio.sleep(delays["claim"])
        return None

    async def connect(org_id: Any = None) -> FakeAI:
        await asyncio.sleep(delays["connect"])
        w.mark("openai_ready")
        return w.ai

    async def instructions(*a: Any, **k: Any) -> str:
        return "BASE INSTRUCTIONS"

    async def resolve_org(*a: Any, **k: Any) -> uuid.UUID:
        return ORG

    async def no_live(*a: Any, **k: Any) -> None:
        return None

    def no_db() -> Any:
        raise RuntimeError("no database in this test")

    async def record_nothing() -> None:
        return None

    monkeypatch.setattr(adapter, "open_voice_conversation", open_conversation)
    monkeypatch.setattr(adapter, "close_voice_conversation", close_conversation)
    monkeypatch.setattr(adapter, "_persist_voice_turn", persist)
    monkeypatch.setattr(bridge, "_call_context", call_context)
    monkeypatch.setattr(bridge, "_load_org_greeting", load_greeting)
    monkeypatch.setattr(bridge, "_load_org_voice", load_voice)
    monkeypatch.setattr(bridge, "claim_precall_connect", claim)
    monkeypatch.setattr(bridge, "_connect_to_openai", connect)
    monkeypatch.setattr(bridge, "_system_instructions", instructions)
    monkeypatch.setattr(bridge, "_resolve_org_id", resolve_org)
    monkeypatch.setattr(bridge, "_start_live_transcriber", no_live)
    monkeypatch.setattr(bridge, "AsyncSessionLocal", no_db)
    monkeypatch.setattr(bridge, "record_error", record_nothing)
    monkeypatch.setattr(bridge, "greeting_cache", w.cache)
    return w


def prime_cache(w: World, voice: Any = None) -> None:
    w.cache._entries[w.cache.key(ORG, GREETING, voice)] = CachedGreeting(text=GREETING, chunks=CHUNKS)


async def run_call(w: World, ws: CallWS, *, hold: float = 0.8) -> None:
    task = asyncio.create_task(bridge.voice_stream(ws))  # type: ignore[arg-type]
    await asyncio.sleep(hold)
    ws.hangup.set()
    await asyncio.wait_for(task, 5)


# --- the greeting reaches the caller before the slow parts finish -----------------------------------------------------------


async def test_a_cached_greeting_is_audible_before_the_conversation_row_exists(world: World) -> None:
    prime_cache(world)
    ws = CallWS()
    await run_call(world, ws)
    first_audio = ws.audio()[0][0]
    assert len(ws.audio()) == len(CHUNKS)
    assert first_audio < world.at("conversation_opened")  # audio did not wait for the (slow) database writes
    assert ws.audio()[0][1]["media"]["contentType"] == "audio/x-mulaw"


async def test_the_model_is_told_what_was_said_and_never_greets_again(world: World) -> None:
    prime_cache(world)
    await run_call(world, CallWS())
    items = [m for m in world.ai.sent if m["type"] == "conversation.item.create"]
    assert len(items) == 1 and items[0]["item"]["role"] == "assistant" and items[0]["item"]["content"][0]["text"] == GREETING
    assert "response.create" not in world.ai.types()  # no second, spoken greeting
    assert world.persisted == [GREETING]  # persisted exactly once


async def test_the_greeting_is_guarded_from_the_callees_first_hello(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    prime_cache(world)
    seen: dict[str, Any] = {}
    real_adopt = adapter.adopt_early_greeting

    async def spy(state: Any, oai: Any, early: Any, log: Any) -> None:
        await real_adopt(state, oai, early, log)
        seen.update(until=state.greeting_guard_until, end=early.playback_end, last=state.last_spoken_text)

    monkeypatch.setattr(adapter, "adopt_early_greeting", spy)
    await run_call(world, CallWS())
    assert seen["until"] >= seen["end"] - 1e-6 and seen["last"] == GREETING


async def test_a_cache_miss_falls_back_to_the_live_greeting_and_starts_filling_the_cache(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    filled: list[str] = []
    monkeypatch.setattr(world.cache, "ensure_in_background", lambda org, text, voice=None: filled.append(text))
    ws = CallWS()
    await run_call(world, ws)
    assert ws.audio() == [] and filled and set(filled) == {GREETING}  # asked to fill (idempotently)
    created = [m for m in world.ai.sent if m["type"] == "response.create"]
    assert len(created) == 1 and GREETING in created[0]["response"]["instructions"]  # spoken once, by the model


async def test_the_cache_flag_off_changes_nothing(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    prime_cache(world)
    monkeypatch.setattr(settings, "voice_greeting_audio_cache", False)
    ws = CallWS()
    await run_call(world, ws)
    assert ws.audio() == [] and world.ai.types().count("response.create") == 1


@pytest.mark.parametrize("policy", [{"recording_mode": "consent"}, {"recording_mode": "notice"}])
async def test_a_consent_or_notice_org_never_uses_the_fixed_audio(world: World, monkeypatch: pytest.MonkeyPatch, policy: dict[str, str]) -> None:
    prime_cache(world)

    async def call_context(org_id: Any, remote: Any, **kw: Any) -> Any:
        return "Veerox", compliance.resolve_recording_policy(policy, "+14155550123")

    monkeypatch.setattr(bridge, "_call_context", call_context)
    ws = CallWS({"from": "+14155550123", "call_uuid": CALL, "provider": "plivo", "org_id": str(ORG)})
    await run_call(world, ws)
    if policy["recording_mode"] == "consent":
        assert ws.audio() == []  # the model must speak first to ask for consent


async def test_a_different_greeting_or_voice_is_a_different_cache_entry(world: World) -> None:
    prime_cache(world)
    assert world.cache.get(ORG, GREETING) is not None
    assert world.cache.get(ORG, GREETING + " Aur ek baat.") is None  # edited greeting: the old audio is never replayed
    assert world.cache.get(uuid.uuid4(), GREETING) is None  # another org
    settings.elevenlabs_voice_id, original = "OtherVoiceId1234567", settings.elevenlabs_voice_id
    try:
        assert world.cache.get(ORG, GREETING) is None  # changed voice: miss
    finally:
        settings.elevenlabs_voice_id = original


# --- start-up work overlaps ------------------------------------------------------------------------------------------------------------


async def test_the_database_reads_run_together_not_one_after_another(world: World) -> None:
    world.delays["conversation"] = 0.3  # type: ignore[attr-defined]
    world.delays["context"] = 0.3  # type: ignore[attr-defined]
    ws = CallWS()
    started = time.monotonic()
    await run_call(world, ws, hold=0.2)
    opened = world.at("conversation_opened") - started
    assert opened < 0.55, f"conversation (0.3 s), context, greeting and voice reads (0.3 s each) ran serially: {opened:.2f} s"


async def test_a_slow_openai_session_does_not_hold_back_the_cached_greeting(world: World) -> None:
    prime_cache(world)
    world.delays["connect"] = 0.6  # type: ignore[attr-defined]
    ws = CallWS()
    await run_call(world, ws, hold=1.0)
    assert ws.audio()[0][0] < world.at("openai_ready")
    assert "response.create" not in world.ai.types()


async def test_the_startup_timeline_is_logged_once_with_ordered_stages(world: World) -> None:
    import structlog

    prime_cache(world)
    with structlog.testing.capture_logs() as logs:
        await run_call(world, CallWS())
    timeline = [e for e in logs if e["event"] == "voice_startup_timeline"]
    assert len(timeline) == 1
    t = timeline[0]
    stages = [k for k in t if k.endswith("_ms")]
    values = [t[k] for k in stages]
    assert "stream_accepted_ms" in stages and "greeting_started_ms" in stages and "first_audio_sent_ms" in stages
    assert t["first_audio_sent_ms"] < t["conversation_opened_ms"]  # the cached audio went out first
    assert all(isinstance(v, int) for v in values)
    text = json.dumps(timeline, default=str)  # (other lines carry the caller; the privacy processor masks them on output)
    assert PHONE not in text and "QUJD" not in text and GREETING not in text  # no number, no audio, no words
    assert latency._STARTUPS == {}  # forgotten once logged


# --- failure and hangup during start-up -----------------------------------------------------------------------------------------------


async def test_a_hangup_during_startup_leaves_no_task_and_closes_a_finished_conversation(world: World) -> None:
    prime_cache(world)
    world.delays["conversation"] = 0.0  # type: ignore[attr-defined]
    ws = CallWS(disconnect_on_send=True)
    before = {t for t in asyncio.all_tasks()}
    task = asyncio.create_task(bridge.voice_stream(ws))  # type: ignore[arg-type]
    await asyncio.wait_for(task, 5)
    await asyncio.sleep(0.05)
    leaked = [t for t in asyncio.all_tasks() - before if not t.done() and t is not asyncio.current_task()]
    assert leaked == []
    assert len(world.closed_conversations) == 1  # the row that was created is closed, not left open


async def test_a_hangup_while_the_conversation_row_is_still_being_written_cancels_it(world: World) -> None:
    prime_cache(world)
    world.delays["conversation"] = 5.0  # type: ignore[attr-defined]
    ws = CallWS(disconnect_on_send=True)
    await asyncio.wait_for(bridge.voice_stream(ws), 5)  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    assert world.closed_conversations == []  # nothing was created, so nothing to close
    assert "conversation_opened" not in world.order()


async def test_a_socket_failure_after_some_audio_is_adopted_and_never_spoken_twice(world: World) -> None:
    prime_cache(world)
    ws = CallWS(fail_on_send=1)  # the second chunk fails
    await run_call(world, ws)
    assert len(ws.audio()) == 1
    assert "response.create" not in world.ai.types()  # the caller already heard the start of it
    assert world.persisted == [GREETING]


async def test_a_failure_before_any_audio_falls_back_to_the_live_greeting(world: World) -> None:
    prime_cache(world)
    ws = CallWS(fail_on_send=0)
    await run_call(world, ws)
    assert world.ai.types().count("response.create") == 1


# --- the early sender in isolation ---------------------------------------------------------------------------------------------------------


async def test_the_early_sender_uses_each_providers_envelope_and_counts_playback() -> None:
    ws = CallWS()
    early = await adapter.send_cached_audio_early(ws, "plivo", None, GREETING, CHUNKS)  # type: ignore[arg-type]
    assert early.chunks == 3 and early.playback_end > time.monotonic()
    assert [m["event"] for _, m in ws.sent] == ["playAudio"] * 3
    twilio = CallWS()
    await adapter.send_cached_audio_early(twilio, "twilio", "MZ123", GREETING, CHUNKS)  # type: ignore[arg-type]
    assert twilio.sent[0][1] == {"event": "media", "streamSid": "MZ123", "media": {"payload": CHUNKS[0]}}


# --- the pre-connected session and the cache warm-up --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("ring", "expected"), [(25, 60.0), (10, 45.0), (40, 75.0), (0, 20.0), (-5, 20.0)])
def test_the_preconnected_session_outlives_the_requested_ringing(monkeypatch: pytest.MonkeyPatch, ring: int, expected: float) -> None:
    monkeypatch.setattr(settings, "voice_outbound_ring_timeout_seconds", ring)
    assert bridge._precall_timeout_seconds() == expected  # without a requested ring timeout the long-standing 20 s stays


async def test_a_call_answered_after_24_seconds_still_finds_its_preconnected_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Phase 8K call rang for 24 s; the old flat 20 s lifetime had already closed the session."""
    monkeypatch.setattr(settings, "voice_outbound_ring_timeout_seconds", 25)
    monkeypatch.setattr(settings, "voice_greeting_audio_cache", False)
    ai = FakeAI()

    async def instructions(*a: Any, **k: Any) -> str:
        return "BASE"

    async def connect(org_id: Any = None) -> FakeAI:
        return ai

    async def context(org_id: Any, remote: Any) -> Any:
        return "Veerox", compliance.resolve_recording_policy(None, remote)

    monkeypatch.setattr(bridge, "_system_instructions", instructions)
    monkeypatch.setattr(bridge, "_connect_to_openai", connect)
    monkeypatch.setattr(bridge, "_call_context", context)
    # Compress time: the lifetime function returns what 24 s of ringing means relative to the real one (60 s) as 0.24 of 0.6 s.
    real = bridge._precall_timeout_seconds
    monkeypatch.setattr(bridge, "_precall_timeout_seconds", lambda: real() / 100)
    bridge._pending_precalls.pop("ring-1", None)
    bridge.start_precall_connect("ring-1", None, ORG, remote_number=PHONE)
    await asyncio.sleep(0.24)  # "24 seconds" of ringing, then the callee answers
    claimed = await bridge.claim_precall_connect("ring-1", ORG)
    assert claimed is not None and claimed[0] is ai and not ai.closed
    bridge._pending_precalls.pop("ring-1", None)


async def test_an_unclaimed_preconnected_session_is_still_closed_eventually(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_greeting_audio_cache", False)
    ai = FakeAI()

    async def instructions(*a: Any, **k: Any) -> str:
        return "BASE"

    async def connect(org_id: Any = None) -> FakeAI:
        return ai

    async def context(org_id: Any, remote: Any) -> Any:
        return "Veerox", compliance.resolve_recording_policy(None, remote)

    monkeypatch.setattr(bridge, "_system_instructions", instructions)
    monkeypatch.setattr(bridge, "_connect_to_openai", connect)
    monkeypatch.setattr(bridge, "_call_context", context)
    monkeypatch.setattr(bridge, "_precall_timeout_seconds", lambda: 0.2)
    bridge.start_precall_connect("ring-2", None, ORG, remote_number=PHONE)
    await asyncio.sleep(0.5)
    assert ai.closed and "ring-2" not in bridge._pending_precalls  # no billed session is left open


async def test_the_greeting_cache_warms_while_the_openai_connect_is_still_in_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_greeting_audio_cache", True)
    events: list[str] = []
    ai = FakeAI()

    async def instructions(*a: Any, **k: Any) -> str:
        return "BASE"

    async def connect(org_id: Any = None) -> FakeAI:
        await asyncio.sleep(0.3)
        events.append("connected")
        return ai

    async def context(org_id: Any, remote: Any) -> Any:
        return "Veerox", compliance.resolve_recording_policy(None, remote)

    async def greeting(org_id: Any) -> str:
        return GREETING

    async def voice(org_id: Any) -> None:
        return None

    class Cache:
        def ensure_in_background(self, org: Any, text: str, v: Any = None) -> None:
            events.append("warm")

    monkeypatch.setattr(bridge, "_system_instructions", instructions)
    monkeypatch.setattr(bridge, "_connect_to_openai", connect)
    monkeypatch.setattr(bridge, "_call_context", context)
    monkeypatch.setattr(bridge, "_load_org_greeting", greeting)
    monkeypatch.setattr(bridge, "_load_org_voice", voice)
    monkeypatch.setattr(bridge, "greeting_cache", Cache())
    bridge._pending_precalls.pop("warm-1", None)
    bridge.start_precall_connect("warm-1", None, ORG, remote_number=PHONE)
    await asyncio.sleep(0.6)
    assert events[0] == "warm" and "connected" in events  # synthesis began first, not after the connect
    task = bridge._pending_precalls.pop("warm-1", None)
    if task is not None:
        (await task)[0]  # let it settle


# --- the answer webhook no longer waits for bookkeeping ---------------------------------------------------------------------------------------


def _answer_request(org: bool = True) -> Request:
    body = f"CallUUID={CALL}&From=%2B910000000001&To=%2B911234567890&Direction=outbound".encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    query = f"org_id={ORG}".encode() if org else b""
    return Request(
        {"type": "http", "method": "POST", "scheme": "https", "server": ("test", 443), "path": "/voice/answer", "query_string": query,
         "headers": [(b"host", b"test"), (b"content-type", b"application/x-www-form-urlencoded")]},
        receive,
    )


async def test_the_answer_returns_before_the_usage_write_and_the_recording_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (("environment", "dev"), ("plivo_signature_mode", "off"), ("public_base_url", "https://pilot.example.invalid")):
        monkeypatch.setattr(settings, name, value)
    latency._STARTUPS.clear()
    steps: list[str] = []

    class SlowDB:
        async def __aenter__(self) -> "SlowDB":
            await asyncio.sleep(0.5)
            steps.append("db")
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def get(self, *a: Any) -> None:
            return None

    async def usage(*a: Any, **k: Any) -> None:
        steps.append("usage")

    monkeypatch.setattr(webhook, "AsyncSessionLocal", lambda: SlowDB())
    monkeypatch.setattr(webhook, "record_usage", usage)
    monkeypatch.setattr(webhook, "start_precall_connect", lambda *a, **k: steps.append("precall"))
    background = BackgroundTasks()
    started = time.monotonic()
    response = await webhook.answer(_answer_request(), background)
    elapsed = time.monotonic() - started
    assert response.status_code == 200 and b"<Stream" in response.body
    assert elapsed < 0.3 and "db" not in steps and "usage" not in steps  # the markup did not wait for the database
    assert "precall" in steps
    await background()  # what the server does once the response has gone out
    assert "usage" in steps and steps.count("db") >= 2
    snapshot_stages = set(latency.startup_snapshot(CALL))
    assert {"answer_received", "ticket_checked", "answer_served"} <= snapshot_stages


# --- the timeline helpers ---------------------------------------------------------------------------------------------------------------------------


def test_startup_marks_are_bounded_ordered_and_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    latency._STARTUPS.clear()
    monkeypatch.setattr(settings, "voice_latency_tracking", True)
    latency.startup_mark("a", "answer_received", at=10.0)
    latency.startup_mark("a", "stream_accepted", at=10.5)
    latency.startup_mark("a", "stream_accepted", at=99.0)  # first mark of a stage wins
    assert latency.startup_snapshot("a") == {"answer_received": 0, "stream_accepted": 500}
    for i in range(100):
        latency.startup_mark(f"c{i}", "answer_received")
    assert len(latency._STARTUPS) <= 32
    latency.startup_mark(None, "answer_received")
    latency.startup_mark("", "answer_received")
    monkeypatch.setattr(settings, "voice_latency_tracking", False)
    latency.startup_mark("off", "answer_received")
    assert "off" not in latency._STARTUPS


# --- a model of the live call's link (about 0.8 s per database round trip) ----------------------------------------------------------------------


async def test_time_to_first_audio_under_the_slow_link_of_the_live_call(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    """Delays copied from the Phase 8K log: one database round trip ~0.8 s, the conversation open ~5 round trips. The old
    serial start-up would need conversation + context + greeting + voice reads one after another (4.0 + 0.8 + 0.8 = 5.6 s)
    before the live greeting could even be requested. This measures OUR ordering under those delays, not the real network."""
    world.delays.update(conversation=4.0, context=0.8)  # type: ignore[attr-defined]
    prime_cache(world)
    cached = CallWS()
    t0 = time.monotonic()
    task = asyncio.create_task(bridge.voice_stream(cached))  # type: ignore[arg-type]
    while not cached.audio() and time.monotonic() - t0 < 6:
        await asyncio.sleep(0.02)
    cached_first = cached.audio()[0][0] - t0
    cached.hangup.set()
    await asyncio.wait_for(task, 10)
    with capsys.disabled():
        print(f"\n[8L] slow-link model, cached greeting: first audio {cached_first:.2f} s after the stream opened (serial baseline >= 5.6 s)")
    assert cached_first < 1.2

    world.cache._entries.clear()
    live = CallWS()
    t0 = time.monotonic()
    task = asyncio.create_task(bridge.voice_stream(live))  # type: ignore[arg-type]
    while "response.create" not in world.ai.types() and time.monotonic() - t0 < 8:
        await asyncio.sleep(0.02)
    requested = time.monotonic() - t0
    live.hangup.set()
    await asyncio.wait_for(task, 10)
    with capsys.disabled():
        print(f"[8L] slow-link model, live greeting: requested {requested:.2f} s after the stream opened (serial baseline >= 5.6 s)")
    assert requested < 4.6
