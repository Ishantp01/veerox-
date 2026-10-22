"""Phase 9: production safeguards around the voice stream: capacity, maximum duration, leg cleanup, and replay state that is safe
to run without a one-time initialisation. Fake sockets and providers only; no live call, no paid request.

Every safeguard here ships OFF (0 / false) and changes nothing until an operator sets it. The tests prove the behaviour when it
is on and that it is inert when it is off.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from apps.api import pilot
from apps.api.channels.voice import call_control, capacity, failover, plivo_client, signature
from apps.api.channels.voice import realtime_bridge as bridge
from apps.api.config import Settings, settings
from apps.api.core.org_credentials import PlivoCredentials
from apps.api.tests.test_voice_startup_8l import CHUNKS, GREETING, ORG, CallWS, World, prime_cache, world  # noqa: F401

fakeredis = pytest.importorskip("fakeredis.aioredis")

PHONE = "+911234567890"
REAL_LOADERS = (bridge._load_org_greeting, bridge._load_org_voice, bridge._call_context)  # captured before any fixture replaces them


def params(call: str, org: Any = ORG, *, frm: str = PHONE) -> dict[str, str]:
    return {"from": frm, "call_uuid": call, "provider": "plivo", "org_id": str(org)}


@pytest.fixture(autouse=True)
def clean_capacity() -> Any:
    capacity._active.clear()
    yield
    capacity._active.clear()


# --- the defaults leave a running deployment alone ---------------------------------------------------------------------------------------------


def test_every_new_safeguard_ships_switched_off() -> None:
    fields = Settings.model_fields
    assert fields["voice_max_active_streams"].default == 0
    assert fields["voice_max_call_seconds"].default == 0
    assert fields["voice_hangup_on_stream_end"].default is False
    assert fields["plivo_signature_mode"].default == "auto"
    assert fields["plivo_replay_state"].default == "auto"


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "dev", ""])
def test_outside_the_pilot_signatures_stay_off_until_an_operator_opts_in(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "plivo_signature_mode", "auto")
    assert signature.effective_mode() == "off" and signature.strict_state() is False
    monkeypatch.setattr(settings, "plivo_signature_mode", "warn")
    assert signature.effective_mode() == "warn"


def test_the_replay_state_follows_the_environment_unless_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    for environment, configured, strict in (
        ("pilot", "auto", True), ("production", "auto", False), ("production", "strict", True), ("pilot", "expiring", False),
        ("production", "garbage", False), ("pilot", "garbage", True),
    ):
        monkeypatch.setattr(settings, "environment", environment)
        monkeypatch.setattr(settings, "plivo_replay_state", configured)
        assert signature.strict_state() is strict, (environment, configured)


# --- capacity ------------------------------------------------------------------------------------------------------------------------------------------


def test_the_capacity_counter_is_exact_and_unlimited_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_max_active_streams", 0)
    assert all(capacity.admit(f"c{i}") for i in range(200)) and capacity.active_count() == 200
    capacity._active.clear()
    monkeypatch.setattr(settings, "voice_max_active_streams", 3)
    assert [capacity.admit(f"c{i}") for i in range(5)] == [True, True, True, False, False]
    assert capacity.admit("c1") is True and capacity.active_count() == 3  # a call that holds a slot does not take another
    capacity.release("c0")
    capacity.release("c0")  # releasing twice is harmless
    assert capacity.admit("c9") is True and capacity.admit("c10") is False
    snap = capacity.snapshot()
    assert snap["active_streams"] == 3 and snap["max_active_streams"] == 3 and set(snap) == {"active_streams", "max_active_streams", "oldest_stream_seconds"}


async def test_a_call_over_the_limit_is_refused_before_any_ai_session_or_row_exists(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_max_active_streams", 2)
    prime_cache(world)
    calls = [CallWS(params(f"call-{i}")) for i in range(3)]
    tasks = [asyncio.create_task(bridge.voice_stream(ws)) for ws in calls[:2]]  # type: ignore[arg-type]
    await asyncio.sleep(0.15)
    assert capacity.active_count() == 2
    connects_before = [e for e in world.events if e[0] == "openai_ready"]
    third = await asyncio.wait_for(bridge.voice_stream(calls[2]), 2)  # type: ignore[arg-type]
    assert third is None and calls[2].closed == 1013 and calls[2].audio() == []  # told to try again later, nothing was started
    assert len([e for e in world.events if e[0] == "openai_ready"]) == len(connects_before)
    for ws in calls[:2]:
        ws.hangup.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    assert capacity.active_count() == 0  # both gave their slots back
    again = CallWS(params("call-3"))
    task = asyncio.create_task(bridge.voice_stream(again))  # type: ignore[arg-type]
    await asyncio.sleep(0.15)
    assert capacity.active_count() == 1 and again.closed is None  # a slot was free again
    again.hangup.set()
    await asyncio.wait_for(task, 5)


async def test_many_simultaneous_calls_each_hear_only_their_own_greeting_and_get_their_own_conversation(world: World) -> None:
    prime_cache(world)
    world.delays["conversation"] = 0.05  # type: ignore[attr-defined]
    calls = [CallWS(params(f"call-{i}", uuid.uuid4())) for i in range(12)]
    tasks = [asyncio.create_task(bridge.voice_stream(ws)) for ws in calls]  # type: ignore[arg-type]
    await asyncio.sleep(0.6)
    assert capacity.active_count() == 12
    assert all(len(ws.audio()) == len(CHUNKS) for ws in calls)  # every caller heard exactly one greeting, on its own socket
    for ws in calls:
        ws.hangup.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 10)
    assert capacity.active_count() == 0
    assert len(set(world.closed_conversations)) == 12 == len(world.closed_conversations)  # twelve distinct rows, each closed once


async def test_a_new_outbound_dial_is_refused_when_the_process_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_max_active_streams", 1)
    reached: list[str] = []

    async def provider(*a: Any, **k: Any) -> dict[str, Any]:
        reached.append("plivo")
        return {"request_uuid": "x"}

    monkeypatch.setattr(plivo_client, "initiate_call", provider)
    creds = PlivoCredentials(auth_id="a", auth_token="b")
    await failover.initiate_call(creds, None, PHONE, "https://x.invalid/voice/answer", plivo_from_number="+910000000001")
    assert reached == ["plivo"]
    capacity.admit("live-call")
    with pytest.raises(capacity.CapacityReached):
        await failover.initiate_call(creds, None, PHONE, "https://x.invalid/voice/answer", plivo_from_number="+910000000001")
    assert reached == ["plivo"]  # the customer's phone was not rung
    assert issubclass(capacity.CapacityReached, RuntimeError)  # callers that already catch RuntimeError keep working


# --- maximum call duration --------------------------------------------------------------------------------------------------------------------


async def test_a_call_is_hung_up_at_the_maximum_duration_and_the_watchdog_leaves_no_task(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_max_call_seconds", 0.3)
    requests: list[dict[str, Any]] = []

    def record(state: Any, log: Any, **kw: Any) -> None:
        requests.append(kw)

    monkeypatch.setattr(call_control, "request_hangup", record)
    prime_cache(world)
    ws = CallWS(params("long-call"))
    task = asyncio.create_task(bridge.voice_stream(ws))  # type: ignore[arg-type]
    await asyncio.sleep(0.7)
    assert len(requests) == 1 and requests[0]["reason"] == "max_duration" and requests[0]["abortable"] is False
    ws.hangup.set()
    await asyncio.wait_for(task, 5)
    await asyncio.sleep(0.05)
    assert not [t for t in asyncio.all_tasks() if "_max_duration_watchdog" in repr(t) and not t.done()]


async def test_a_call_that_ends_early_cancels_the_watchdog_and_never_hangs_up(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_max_call_seconds", 5)
    requests: list[Any] = []
    monkeypatch.setattr(call_control, "request_hangup", lambda *a, **k: requests.append(k))
    prime_cache(world)
    ws = CallWS(params("short-call"))
    task = asyncio.create_task(bridge.voice_stream(ws))  # type: ignore[arg-type]
    await asyncio.sleep(0.3)
    ws.hangup.set()
    await asyncio.wait_for(task, 5)
    await asyncio.sleep(0.05)
    assert requests == [] and not [t for t in asyncio.all_tasks() if "_max_duration_watchdog" in repr(t) and not t.done()]


async def test_the_duration_limit_off_creates_nothing(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_max_call_seconds", 0)
    prime_cache(world)
    ws = CallWS(params("c"))
    task = asyncio.create_task(bridge.voice_stream(ws))  # type: ignore[arg-type]
    await asyncio.sleep(0.2)
    assert not [t for t in asyncio.all_tasks() if "_max_duration_watchdog" in repr(t)]
    ws.hangup.set()
    await asyncio.wait_for(task, 5)


# --- ending the provider leg when the stream ends (a restart must not leave a silent, billed line) ---------------------------------------------------


async def test_when_the_stream_ends_the_provider_leg_is_ended_too(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_hangup_on_stream_end", True)
    ended: list[str] = []

    async def provider_hangup(state: Any, log: Any) -> str:
        ended.append(state.call_uuid)
        return "ok"

    monkeypatch.setattr(call_control, "provider_hangup", provider_hangup)
    prime_cache(world)
    ws = CallWS(params("restart-call"))
    await asyncio.wait_for(_run(ws, 0.4), 5)
    assert ended == ["restart-call"] and len(world.closed_conversations) == 1


async def _run(ws: CallWS, hold: float) -> None:
    task = asyncio.create_task(bridge.voice_stream(ws))  # type: ignore[arg-type]
    await asyncio.sleep(hold)
    ws.hangup.set()
    await task


async def test_ending_the_leg_is_off_by_default_and_never_blocks_cleanup(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def provider_hangup(state: Any, log: Any) -> str:
        calls.append("hangup")
        await asyncio.sleep(30)
        return "ok"

    monkeypatch.setattr(call_control, "provider_hangup", provider_hangup)
    prime_cache(world)
    await asyncio.wait_for(_run(CallWS(params("a")), 0.3), 5)
    assert calls == []  # off: not asked
    monkeypatch.setattr(settings, "voice_hangup_on_stream_end", True)
    monkeypatch.setattr(bridge, "_STREAM_END_HANGUP_TIMEOUT", 0.2)
    started = time.monotonic()
    await asyncio.wait_for(_run(CallWS(params("b")), 0.3), 5)
    assert calls == ["hangup"] and time.monotonic() - started < 1.5  # a provider that hangs cannot hold the shutdown
    assert capacity.active_count() == 0 and len(world.closed_conversations) == 2


async def test_a_provider_error_while_ending_the_leg_is_contained(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_hangup_on_stream_end", True)

    async def provider_hangup(state: Any, log: Any) -> str:
        raise RuntimeError("Plivo is down")

    monkeypatch.setattr(call_control, "provider_hangup", provider_hangup)
    prime_cache(world)
    await asyncio.wait_for(_run(CallWS(params("c")), 0.3), 5)
    assert capacity.active_count() == 0 and len(world.closed_conversations) == 1


# --- replay state that needs no initialisation (production) ------------------------------------------------------------------------------------------


@pytest.fixture
async def prod(monkeypatch: pytest.MonkeyPatch) -> Any:
    redis = fakeredis.FakeRedis(decode_responses=True)
    for name, value in (("environment", "production"), ("plivo_signature_mode", "enforce"), ("plivo_replay_state", "auto"),
                        ("secret_encryption_key", "synthetic-key"), ("plivo_auth_token", "synthetic-token")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(signature, "get_redis_pool", lambda: redis)
    yield redis
    await redis.aclose()


async def test_production_nonces_expire_instead_of_filling_a_ledger_and_need_no_initialisation(prod: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert await prod.dbsize() == 0  # nothing was initialised
    assert (await signature.claim_nonce("n-1")).ok
    assert await signature.claim_nonce("n-1") == signature.Verdict(False, "replayed_nonce")
    assert (await signature.claim_nonce("n-2")).ok
    keys = [k async for k in prod.scan_iter()]
    assert len(keys) == 2
    for key in keys:
        assert 0 < await prod.ttl(key) <= settings.plivo_nonce_expiring_ttl_seconds
    await prod.pexpire(keys[0], 20)  # time passes: the record goes away on its own, so storage cannot grow without bound
    await asyncio.sleep(0.05)
    assert await prod.dbsize() == 1


async def test_production_concurrent_deliveries_of_one_request_are_claimed_once(prod: Any) -> None:
    results = await asyncio.gather(*(signature.claim_nonce("same-nonce") for _ in range(25)))
    assert sum(r.ok for r in results) == 1


async def test_production_stream_leases_are_per_call_exclusive_released_and_self_expiring(prod: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def issue(call: str, org: str = "org-1") -> dict[str, str]:
        return signature.issue_stream_params(call, PHONE, org, None, "plivo")

    async def take(call: str, org: str = "org-1", p: dict[str, str] | None = None, leases: list[Any] | None = None) -> bool:
        p = p or issue(call, org)
        return await signature.stream_allowed(p["sig"], p["exp"], call, PHONE, org, None, "plivo", leases=leases)

    assert await prod.dbsize() == 0
    held: list[Any] = []
    assert await take("call-A", leases=held)
    assert not await take("call-A")  # the same call cannot have two live streams
    assert await take("call-B") and await take("call-C", "org-2")  # other calls, other orgs, are unaffected
    stolen = issue("call-A")
    assert not await take("call-A", "org-2", stolen)  # a token for another org is refused before any lease is touched
    assert await signature.release_stream(held[0])
    assert not await signature.release_stream(held[0])  # a second release finds nothing: nobody else's lease is removed
    assert await take("call-A")  # a clean reconnect after the release
    leases = [k async for k in prod.scan_iter()]
    assert leases
    for key in leases:  # every lease expires on its own
        assert 0 < await prod.ttl(key) <= settings.plivo_stream_lease_seconds


async def test_a_crashed_process_cannot_lock_a_call_forever_in_production(prod: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    p = signature.issue_stream_params("call-X", PHONE, "org-1", None, "plivo")
    held: list[Any] = []
    assert await signature.stream_allowed(p["sig"], p["exp"], "call-X", PHONE, "org-1", None, "plivo", leases=held)
    await prod.pexpire(held[0].key, 20)  # the owner died without releasing; its lease runs out by itself
    await asyncio.sleep(0.05)
    assert await signature.stream_allowed(p["sig"], p["exp"], "call-X", PHONE, "org-1", None, "plivo")
    assert not await signature.release_stream(held[0])  # the dead owner cannot release the replacement's lease


async def test_production_replay_state_fails_closed_when_redis_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (("environment", "production"), ("plivo_signature_mode", "enforce"), ("secret_encryption_key", "k")):
        monkeypatch.setattr(settings, name, value)

    def down() -> Any:
        raise ConnectionError("redis down")

    monkeypatch.setattr(signature, "get_redis_pool", down)
    assert (await signature.claim_nonce("n")) == signature.Verdict(False, "nonce_store_error")
    p = signature.issue_stream_params("c", PHONE, "o", None, "plivo")
    assert not await signature.stream_allowed(p["sig"], p["exp"], "c", PHONE, "o", None, "plivo")


async def test_a_dialled_production_call_carries_a_single_use_ticket_when_signatures_are_enforced(prod: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_base_url", "https://api.example.invalid")
    url = await signature.attach_call_ticket(f"https://api.example.invalid/voice/answer?org_id={ORG}", PHONE)
    assert parse_qs(urlsplit(url).query).get("ct")
    keys = [k async for k in prod.scan_iter()]
    assert len(keys) == 1 and 0 < await prod.ttl(keys[0]) <= settings.plivo_call_ticket_ttl_seconds
    monkeypatch.setattr(settings, "plivo_signature_mode", "auto")  # the default in production: nothing changes
    assert await signature.attach_call_ticket(f"https://api.example.invalid/voice/answer?org_id={ORG}", PHONE) == \
        f"https://api.example.invalid/voice/answer?org_id={ORG}"


# --- the pilot keeps its strict behaviour -------------------------------------------------------------------------------------------------------


async def test_the_pilot_still_requires_the_initialised_strict_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    for name, value in (("environment", "pilot"), ("plivo_replay_state", "auto"), ("secret_encryption_key", "k")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(signature, "get_redis_pool", lambda: redis)
    assert pilot.pilot_mode() and signature.strict_state()
    assert (await signature.claim_nonce("n")) == signature.Verdict(False, "nonce_state_missing")  # nothing is created implicitly
    assert await redis.dbsize() == 0
    await redis.aclose()


# --- the configuration check says what a deployment still lacks -----------------------------------------------------------------------------------


def _codes(**overrides: Any) -> dict[str, str]:
    from types import SimpleNamespace

    from apps.api.channels.voice.config_check import check_voice_config

    base = dict(
        environment="production", plivo_signature_mode="auto", twilio_account_sid=None, twilio_auth_token=None, plivo_auth_token="t",
        public_base_url="https://api.workassignai.com", voice_max_active_streams=0, voice_max_call_seconds=0, voice_hangup_on_stream_end=False,
        voice_tts_provider="openai", openai_api_key="k",
    )
    base.update(overrides)
    return {i.code: i.level for i in check_voice_config(SimpleNamespace(**base))}


def test_an_unauthenticated_production_is_reported_with_the_rollout_path() -> None:
    codes = _codes()
    assert codes["webhooks_unauthenticated"] == "warning" and codes["no_stream_cap"] == "info"
    assert codes["no_max_call_duration"] == "info" and codes["leg_not_ended_on_stream_end"] == "info"


def test_enforcing_while_twilio_is_configured_is_an_error_and_warn_only_a_warning() -> None:
    twilio = dict(twilio_account_sid="AC1", twilio_auth_token="x")
    assert _codes(plivo_signature_mode="enforce", **twilio)["signatures_without_twilio_support"] == "error"
    assert _codes(plivo_signature_mode="warn", **twilio)["signatures_without_twilio_support"] == "warning"
    assert "signatures_without_twilio_support" not in _codes(plivo_signature_mode="enforce")
    assert "webhooks_unauthenticated" not in _codes(plivo_signature_mode="enforce")


def test_signature_prerequisites_are_checked() -> None:
    assert "signature_token_missing" in _codes(plivo_signature_mode="warn", plivo_auth_token=None)
    assert "public_base_url_not_root_https" in _codes(plivo_signature_mode="warn", public_base_url="http://api.example.net")
    assert "public_base_url_not_root_https" in _codes(plivo_signature_mode="warn", public_base_url="https://api.example.net/prefix")
    assert "public_base_url_not_root_https" not in _codes(plivo_signature_mode="warn", public_base_url="https://api.example.net/")


def test_the_safeguards_turned_on_clear_their_notices() -> None:
    codes = _codes(voice_max_active_streams=30, voice_max_call_seconds=1800, voice_hangup_on_stream_end=True)
    assert not {"no_stream_cap", "no_max_call_duration", "leg_not_ended_on_stream_end"} & set(codes)


# --- provider failure at call start ---------------------------------------------------------------------------------------------------------------


async def test_an_openai_failure_at_start_ends_the_call_cleanly_and_gives_everything_back(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(org_id: Any = None) -> Any:
        raise ConnectionError("OpenAI unreachable")

    monkeypatch.setattr(bridge, "_connect_to_openai", broken)
    monkeypatch.setattr(settings, "voice_hangup_on_stream_end", True)
    ended: list[str] = []

    async def provider_hangup(state: Any, log: Any) -> str:
        ended.append(state.call_uuid)
        return "ok"

    monkeypatch.setattr(call_control, "provider_hangup", provider_hangup)
    ws = CallWS(params("fail-start"))
    await asyncio.wait_for(bridge.voice_stream(ws), 5)  # type: ignore[arg-type]
    assert capacity.active_count() == 0  # the slot is free again
    assert len(world.closed_conversations) == 1  # the row that was opened is closed, not left dangling
    assert world.ai.sent == []  # nothing was said to a model that never connected
    assert ended == ["fail-start"]  # and the caller is not left on a silent, open line


# --- one organisation read per call start --------------------------------------------------------------------------------------------------------


async def test_the_greeting_voice_and_context_come_from_one_organisation_read(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    greeting, voice, context = REAL_LOADERS
    monkeypatch.setattr(bridge, "_load_org_greeting", greeting)
    monkeypatch.setattr(bridge, "_load_org_voice", voice)
    monkeypatch.setattr(bridge, "_call_context", context)
    org = SimpleNamespace(voice_greeting=GREETING, voice_profile=None, voice_compliance={"recording_mode": "off"}, voice_brand=None, name="Org")
    reads: list[Any] = []

    async def fetch(org_id: Any) -> Any:
        reads.append(org_id)
        await asyncio.sleep(0.05)
        return org

    monkeypatch.setattr(bridge, "_fetch_org", fetch)  # AsyncSessionLocal is unavailable in this fixture: any second read would fail
    prime_cache(world)
    ws = CallWS(params("one-read"))
    task = asyncio.create_task(bridge.voice_stream(ws))  # type: ignore[arg-type]
    await asyncio.sleep(0.6)
    assert reads == [ORG]  # one read, not three
    assert len(ws.audio()) == len(CHUNKS)  # and the cached greeting still reached the caller
    ws.hangup.set()
    await asyncio.wait_for(task, 5)


async def test_a_failed_organisation_read_falls_back_to_the_default_greeting_not_a_failed_call(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    greeting, voice, context = REAL_LOADERS
    monkeypatch.setattr(bridge, "_load_org_greeting", greeting)
    monkeypatch.setattr(bridge, "_load_org_voice", voice)
    monkeypatch.setattr(bridge, "_call_context", context)
    ws = CallWS(params("no-org"))  # AsyncSessionLocal raises, so the real _fetch_org returns None
    task = asyncio.create_task(bridge.voice_stream(ws))  # type: ignore[arg-type]
    await asyncio.sleep(0.6)
    assert world.ai.types().count("response.create") == 1 and ws.audio() == []  # the model speaks its default greeting
    ws.hangup.set()
    await asyncio.wait_for(task, 5)
