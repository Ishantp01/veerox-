"""Phase 5: ending a call on purpose (channels/voice/call_control.py) and how the
adapter triggers it. Provider calls are mocked; nothing here touches a network,
a phone or a paid API.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from apps.api.channels.voice import adapter, call_control, plivo_client, twilio_client
from apps.api.config import settings
from apps.api.core.conversation import voice_decision
from apps.api.core.conversation.silence import SilenceMonitor
from apps.api.core.org_credentials import PlivoCredentials, TwilioCredentials

ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER = uuid.UUID("00000000-0000-0000-0000-000000000ccc")


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def info(self, event: str, *a: Any, **k: Any) -> None:
        self.events.append(event)

    warning = info
    error = info


class _Socket:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _Phone:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(json.loads(message))


class _Agent:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


def make_state(**fields: Any) -> adapter.CallState:
    state = adapter.CallState(
        user_id=USER, conversation_id=uuid.uuid4(), org_id=ORG, base_instructions="BASE", call_uuid="call-1"
    )
    state.call_ws = _Socket()
    for key, value in fields.items():
        setattr(state, key, value)
    return state


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Fake provider + outcome recorder; fast, bounded settings."""
    rec = SimpleNamespace(hangups=[], outcomes=[], results=["ok"])

    async def fake_hangup(state: Any, log: Any) -> str:
        rec.hangups.append(state.call_uuid)
        return rec.results.pop(0) if len(rec.results) > 1 else rec.results[0]

    async def fake_outcome(conversation_id: Any, org_id: Any, outcome: str) -> None:
        rec.outcomes.append((org_id, outcome))

    monkeypatch.setattr(call_control, "provider_hangup", fake_hangup)
    monkeypatch.setattr(call_control, "record_call_outcome", fake_outcome)
    monkeypatch.setattr(settings, "voice_end_call_enabled", True)
    monkeypatch.setattr(settings, "voice_end_call_grace_seconds", 0.0)
    monkeypatch.setattr(settings, "voice_end_call_max_wait_seconds", 2.0)
    return rec


# --- bounds --------------------------------------------------------------------------------------------------------


def test_bounded_clamps_and_survives_garbage() -> None:
    assert call_control.bounded(5, 1, 10, 3) == 5
    assert call_control.bounded(-4, 1, 10, 3) == 1
    assert call_control.bounded(500, 1, 10, 3) == 10
    for junk in (None, "abc", float("nan"), [], object()):
        assert call_control.bounded(junk, 1, 10, 3) == 3


# --- waiting for the goodbye ---------------------------------------------------------------------------------------


async def test_the_goodbye_is_allowed_to_finish_before_the_line_is_cut(provider: SimpleNamespace) -> None:
    state = make_state(response_active=True)
    call_control.request_hangup(state, _Log(), reason="task_complete")
    await asyncio.sleep(0.3)
    assert provider.hangups == []  # still generating the goodbye

    state.response_active = False
    state.playback_end = time.monotonic() + 0.4  # but its audio is still playing
    await asyncio.sleep(0.15)
    assert provider.hangups == []
    await asyncio.sleep(0.6)
    assert provider.hangups == ["call-1"] and state.hangup_done


async def test_the_wait_is_bounded_even_if_playback_never_ends(
    provider: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_end_call_max_wait_seconds", 0.5)
    state = make_state(response_active=True, playback_end=time.monotonic() + 3600)
    started = time.monotonic()
    task = call_control.request_hangup(state, _Log(), reason="task_complete")
    await asyncio.wait_for(task, timeout=3.0)
    assert time.monotonic() - started < 2.0
    assert provider.hangups == ["call-1"]  # a stuck flag cannot keep the line open forever


async def test_a_nonsense_setting_still_leaves_a_bound(
    provider: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_end_call_max_wait_seconds", float("nan"))
    monkeypatch.setattr(settings, "voice_end_call_grace_seconds", -5)
    state = make_state()
    task = call_control.request_hangup(state, _Log(), reason="task_complete")
    await asyncio.wait_for(task, timeout=2.0)  # idle call: ends at once, with defaults for the bad values
    assert provider.hangups == ["call-1"]


# --- idempotency -------------------------------------------------------------------------------------------------------


async def test_repeated_requests_hang_up_once(provider: SimpleNamespace) -> None:
    state, log = make_state(), _Log()
    first = call_control.request_hangup(state, log, reason="task_complete")
    second = call_control.request_hangup(state, log, reason="customer_requested")
    assert second is first  # a pending request is not duplicated
    await first
    third = call_control.request_hangup(state, log, reason="no_response")
    assert third is first  # and neither is one after it finished
    await asyncio.sleep(0.05)
    assert provider.hangups == ["call-1"] and provider.outcomes == [(ORG, "completed")]
    assert log.events.count("voice_hangup_duplicate_ignored") == 2


async def test_it_does_not_ask_the_provider_when_the_caller_already_hung_up(provider: SimpleNamespace) -> None:
    state = make_state(call_ended=True)
    await call_control.request_hangup(state, _Log(), reason="task_complete")
    assert provider.hangups == []  # nothing to hang up
    assert state.hangup_done and provider.outcomes == [(ORG, "completed")]  # but the outcome is still recorded


async def test_an_already_ended_call_counts_as_success_and_closes_the_stream(provider: SimpleNamespace) -> None:
    provider.results = ["already_ended"]
    state = make_state()
    await call_control.request_hangup(state, _Log(), reason="task_complete")
    assert state.hangup_done and state.call_ws.closed


async def test_a_transient_hangup_failure_is_retried_within_the_request(
    provider: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(call_control, "_HANGUP_ROUND_GAP_SECONDS", 0.0)
    provider.results = ["failed", "ok"]
    state, log = make_state(), _Log()
    await call_control.request_hangup(state, log, reason="task_complete")
    assert state.hangup_done and provider.hangups == ["call-1", "call-1"]


async def test_a_persistent_hangup_failure_is_reported_not_hidden_and_can_be_retried(
    provider: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(call_control, "_HANGUP_ROUND_GAP_SECONDS", 0.0)
    provider.results = ["failed"] * 3
    state, log = make_state(), _Log()
    await call_control.request_hangup(state, log, reason="task_complete")
    assert not state.hangup_done and not state.call_ws.closed  # the line may still be open: don't pretend
    assert provider.outcomes == [(ORG, "error")] and "voice_hangup_not_confirmed" in log.events
    assert "voice_hangup_failed_line_may_be_open" in log.events

    provider.results = ["ok"]
    await call_control.request_hangup(state, log, reason="task_complete")  # a later request tries again
    assert state.hangup_done


# --- abort / commit -------------------------------------------------------------------------------------------------------


async def test_a_caller_speaking_again_cancels_an_agent_initiated_hangup(provider: SimpleNamespace) -> None:
    state = make_state(response_active=True)
    task = call_control.request_hangup(state, _Log(), reason="task_complete")
    await asyncio.sleep(0.1)
    assert call_control.abort_hangup(state, _Log()) is True
    await asyncio.sleep(0.05)
    assert task.cancelled() and provider.hangups == [] and state.hangup_task is None
    # ...and the call can still be ended later.
    state.response_active = False
    await call_control.request_hangup(state, _Log(), reason="task_complete")
    assert provider.hangups == ["call-1"]


async def test_a_callers_own_request_to_end_cannot_be_aborted(provider: SimpleNamespace) -> None:
    state = make_state(response_active=True)
    call_control.request_hangup(state, _Log(), reason="customer_requested", abortable=False)
    await asyncio.sleep(0.05)
    assert call_control.abort_hangup(state, _Log()) is False


async def test_nothing_can_abort_once_the_hangup_has_committed(provider: SimpleNamespace) -> None:
    state = make_state()
    task = call_control.request_hangup(state, _Log(), reason="task_complete")
    await task
    assert state.hangup_committed and call_control.abort_hangup(state, _Log()) is False


async def test_after_a_response_the_hangup_waits_for_the_goodbye_not_the_previous_answer(
    provider: SimpleNamespace,
) -> None:
    """The caller said "bye" while the agent was still answering the question before it."""
    state = make_state(response_active=True)
    call_control.request_hangup(state, _Log(), reason="customer_requested", outcome="customer_requested_end",
                                after_response=True, abortable=False)
    state.responses_done += 1  # the earlier answer finishes
    state.response_active = False
    await asyncio.sleep(0.3)
    assert provider.hangups == []  # that was not the goodbye
    state.responses_done += 1  # the goodbye
    await asyncio.sleep(0.4)
    assert provider.hangups == ["call-1"] and provider.outcomes == [(ORG, "customer_requested_end")]


async def test_a_goodbye_that_never_comes_does_not_hold_the_line(
    provider: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_end_call_max_wait_seconds", 0.5)
    state = make_state()
    task = call_control.request_hangup(state, _Log(), reason="customer_requested", after_response=True, abortable=False)
    await asyncio.wait_for(task, timeout=3.0)
    assert provider.hangups == ["call-1"]


async def test_tearing_down_cancels_a_hangup_that_has_not_committed(provider: SimpleNamespace) -> None:
    state = make_state(response_active=True)
    task = call_control.request_hangup(state, _Log(), reason="task_complete")
    await asyncio.sleep(0.05)
    call_control.cancel_pending_hangup(state)
    await asyncio.sleep(0.05)
    assert task.cancelled() and provider.hangups == []


# --- provider calls: Plivo and Twilio independently, with the org's own credentials -----------------------------


@pytest.fixture
def creds(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    seen = SimpleNamespace(plivo=[], twilio=[], plivo_results=["ok"], twilio_results=["ok"])

    async def plivo_hangup(c: Any, call_id: str) -> str:
        seen.plivo.append((c, call_id))
        return seen.plivo_results.pop(0) if len(seen.plivo_results) > 1 else seen.plivo_results[0]

    async def twilio_hangup(c: Any, call_id: str) -> str:
        seen.twilio.append((c, call_id))
        return seen.twilio_results.pop(0) if len(seen.twilio_results) > 1 else seen.twilio_results[0]

    async def org(_: Any) -> Any:
        return SimpleNamespace(id=ORG)

    monkeypatch.setattr(call_control, "_load_org", org)
    monkeypatch.setattr(call_control, "resolve_plivo_credentials", lambda o: PlivoCredentials("pid", "ptok"))
    monkeypatch.setattr(call_control, "resolve_twilio_credentials", lambda o: TwilioCredentials("tsid", "ttok"))
    monkeypatch.setattr(plivo_client, "hangup_call", plivo_hangup)
    monkeypatch.setattr(twilio_client, "hangup_call", twilio_hangup)
    return seen


async def test_plivo_calls_use_plivo_and_the_orgs_credentials(creds: SimpleNamespace) -> None:
    assert await call_control.provider_hangup(make_state(provider="plivo"), _Log()) == "ok"
    assert creds.plivo == [(PlivoCredentials("pid", "ptok"), "call-1")] and creds.twilio == []


async def test_twilio_calls_use_twilio_and_the_orgs_credentials(creds: SimpleNamespace) -> None:
    assert await call_control.provider_hangup(make_state(provider="twilio"), _Log()) == "ok"
    assert creds.twilio == [(TwilioCredentials("tsid", "ttok"), "call-1")] and creds.plivo == []


async def test_a_failed_hangup_is_retried_once_then_reported(creds: SimpleNamespace) -> None:
    creds.plivo_results = ["failed", "ok"]
    assert await call_control.provider_hangup(make_state(), _Log()) == "ok" and len(creds.plivo) == 2
    creds.plivo.clear()
    creds.plivo_results = ["failed"]
    assert await call_control.provider_hangup(make_state(), _Log()) == "failed" and len(creds.plivo) == 2


async def test_without_credentials_or_a_call_id_nothing_is_attempted(
    creds: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert await call_control.provider_hangup(make_state(call_uuid=None), _Log()) == "no_credentials"
    monkeypatch.setattr(call_control, "resolve_plivo_credentials", lambda o: None)
    assert await call_control.provider_hangup(make_state(), _Log()) == "no_credentials"
    assert creds.plivo == []


async def test_a_crash_while_looking_up_credentials_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(_: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(call_control, "_load_org", boom)
    assert await call_control.provider_hangup(make_state(), _Log()) == "failed"


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [(204, "", "ok"), (200, "", "ok"), (404, "", "already_ended"), (410, "", "already_ended"), (500, "", "failed")],
)
async def test_plivo_hangup_result_mapping(monkeypatch: pytest.MonkeyPatch, status: int, body: str, expected: str) -> None:
    monkeypatch.setattr(plivo_client, "_http", _http(lambda req: httpx.Response(status, text=body)))
    assert await plivo_client.hangup_call(PlivoCredentials("pid", "tok"), "c1") == expected


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, "{}", "ok"),
        (404, "", "already_ended"),
        (400, '{"code": 21220, "message": "Call is not in-progress"}', "already_ended"),
        (400, '{"code": 20001, "message": "bad"}', "failed"),
        (500, "", "failed"),
    ],
)
async def test_twilio_hangup_result_mapping(monkeypatch: pytest.MonkeyPatch, status: int, body: str, expected: str) -> None:
    monkeypatch.setattr(twilio_client, "_http", _http(lambda req: httpx.Response(status, text=body)))
    assert await twilio_client.hangup_call(TwilioCredentials("sid", "tok"), "c1") == expected


async def test_a_network_error_is_a_failed_hangup_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(plivo_client, "_http", _http(boom))
    monkeypatch.setattr(twilio_client, "_http", _http(boom))
    assert await plivo_client.hangup_call(PlivoCredentials("p", "t"), "c") == "failed"
    assert await twilio_client.hangup_call(TwilioCredentials("s", "t"), "c") == "failed"


async def test_the_outcome_is_recorded_on_the_conversation_for_its_own_org_only(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.db.models import Conversation, Org, User

    other = uuid.UUID("00000000-0000-0000-0000-000000000002")
    db_session.add_all([Org(id=ORG, name="A"), Org(id=other, name="B")])
    db_session.add(User(id=USER, org_id=ORG, phone="+910000000001"))
    conv = Conversation(org_id=ORG, user_id=USER, channel="voice", tags=["x", "outcome:old"])
    db_session.add(conv)
    await db_session.commit()

    class _Ctx:
        async def __aenter__(self) -> Any:
            return db_session

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(call_control, "AsyncSessionLocal", lambda: _Ctx())
    await call_control.record_call_outcome(conv.id, other, "completed")  # wrong org: ignored
    await db_session.refresh(conv)
    assert conv.tags == ["x", "outcome:old"]
    await call_control.record_call_outcome(conv.id, ORG, "completed")
    await db_session.refresh(conv)
    assert conv.tags == ["x", "outcome:completed"]  # one outcome at a time
    await call_control.record_call_outcome(None, ORG, "completed")  # no conversation: no error


# --- how the adapter triggers it -----------------------------------------------------------------------------------------


class Call:
    def __init__(self, **fields: Any) -> None:
        self.state = make_state(**fields)
        self.state.call_ws = _Socket()
        self.phone, self.agent, self.log = _Phone(), _Agent(), _Log()

    async def event(self, etype: str, **fields: Any) -> None:
        await adapter.handle_openai_event({"type": etype, **fields}, self.phone, self.agent, self.state, self.log)  # type: ignore[arg-type]

    def types(self) -> list[str]:
        return [m["type"] for m in self.agent.sent]

    def outputs(self) -> list[dict[str, Any]]:
        return [json.loads(m["item"]["output"]) for m in self.agent.sent if m["type"] == "conversation.item.create"]


async def test_the_end_call_tool_is_offered_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    base = [t["name"] for t in adapter.realtime_tools()]
    assert "end_call" not in base and "recording_consent" not in base
    monkeypatch.setattr(settings, "voice_end_call_enabled", True)
    assert [t["name"] for t in adapter.realtime_tools()][-1] == "end_call"
    assert [t["name"] for t in adapter.realtime_tools(consent=True)][-2:] == ["end_call", "recording_consent"]
    monkeypatch.setattr(settings, "voice_end_call_enabled", False)
    assert [t["name"] for t in adapter.realtime_tools()] == base  # off again: exactly as before


async def test_end_call_hangs_up_after_the_goodbye_and_keeps_the_agent_quiet(provider: SimpleNamespace) -> None:
    call = Call(response_active=True, resp_text="Dhanyavaad, goodbye!")
    await call.event("response.function_call_arguments.done", name="end_call", call_id="c1",
                     arguments=json.dumps({"reason": "task_complete"}))
    assert call.outputs() == [{"status": "ok", "note": call.outputs()[0]["note"]}]
    assert "response.create" not in call.types()  # no new response: nothing more is said
    assert call.state.hangup_task is not None and provider.hangups == []  # waiting for the goodbye
    await call.event("response.done")
    await asyncio.sleep(0.4)
    assert provider.hangups == ["call-1"] and provider.outcomes == [(ORG, "completed")]


async def test_end_call_with_no_goodbye_yet_asks_for_one_before_hanging_up(provider: SimpleNamespace) -> None:
    call = Call(response_active=True)  # tool response has no spoken goodbye
    await call.event("response.function_call_arguments.done", name="end_call", call_id="c1",
                     arguments=json.dumps({"reason": "task_complete"}))
    assert "response.create" not in call.types()  # never overlap the tool's originating response
    assert call.state.hangup_task is not None and provider.hangups == []
    await call.event("response.done")
    assert call.types().count("response.create") == 1  # request the goodbye only now
    assert provider.hangups == []
    await call.event("response.created", response={"id": "goodbye"})
    await call.event("response.done", response={"id": "goodbye"})
    await asyncio.wait_for(call.state.hangup_task, timeout=3)
    assert provider.hangups == ["call-1"]


async def test_end_call_when_disabled_is_refused_and_the_call_carries_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_end_call_enabled", False)
    call = Call(response_active=True)
    await call.event("response.function_call_arguments.done", name="end_call", call_id="c1", arguments="{}")
    assert call.outputs()[0]["reason"] == "end_call_not_enabled" and call.state.hangup_task is None
    assert "response.create" not in call.types()
    await call.event("response.done")
    assert call.types().count("response.create") == 1


@pytest.mark.parametrize(
    ("reason", "outcome", "abortable"),
    [("task_complete", "completed", True), ("customer_requested", "customer_requested_end", False),
     ("no_response", "no_response", True), ("bogus", "completed", True)],
)
async def test_end_call_reasons_map_to_outcomes(provider: SimpleNamespace, reason: str, outcome: str, abortable: bool) -> None:
    call = Call()
    await call.event("response.function_call_arguments.done", name="end_call", call_id="c1",
                     arguments=json.dumps({"reason": reason}))
    await call.state.hangup_task
    assert provider.outcomes == [(ORG, outcome)] and call.state.hangup_abortable is abortable


async def test_end_call_with_malformed_arguments_does_not_crash(provider: SimpleNamespace) -> None:
    call = Call()
    await call.event("response.function_call_arguments.done", name="end_call", call_id="c1", arguments="{not json")
    await call.state.hangup_task
    assert provider.hangups == ["call-1"]


async def test_a_caller_saying_they_are_done_ends_the_call_after_the_agents_goodbye(provider: SimpleNamespace) -> None:
    call = Call()
    await call.event("conversation.item.input_audio_transcription.completed", transcript="theek hai, phone rakhta hoon")
    assert call.state.hangup_task is not None and call.state.hangup_reason == "customer_requested"
    await asyncio.sleep(0.2)
    assert provider.hangups == []  # waiting for the goodbye response
    call.state.responses_done += 1
    await asyncio.sleep(0.4)
    assert provider.hangups == ["call-1"] and provider.outcomes == [(ORG, "customer_requested_end")]


async def test_an_ordinary_sentence_does_not_end_the_call(provider: SimpleNamespace) -> None:
    call = Call()
    await call.event("conversation.item.input_audio_transcription.completed", transcript="mujhe price batao")
    assert call.state.hangup_task is None


async def test_the_callers_request_is_ignored_while_the_feature_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_end_call_enabled", False)
    call = Call()
    await call.event("conversation.item.input_audio_transcription.completed", transcript="phone rakhta hoon")
    assert call.state.hangup_task is None


async def test_the_caller_speaking_cancels_an_agent_initiated_end_but_not_their_own(provider: SimpleNamespace) -> None:
    call = Call(response_active=True)
    await call.event("response.function_call_arguments.done", name="end_call", call_id="c1",
                     arguments=json.dumps({"reason": "task_complete"}))
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(0.05)
    assert call.state.hangup_task is None and provider.hangups == []  # they have more to say

    other = Call(response_active=True)
    await other.event("conversation.item.input_audio_transcription.completed", transcript="bye bye, ab bas")
    await other.event("input_audio_buffer.speech_started")
    assert other.state.hangup_task is not None  # their own request stands


async def test_giving_up_on_a_silent_line_hangs_up(provider: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_log(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(voice_decision, "log_silence_event", no_log)
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    monkeypatch.setattr(settings, "conversation_silence_check_seconds", 0.05)
    voice_decision.set_cached_voice_mode(ORG, "live")
    call = Call()
    call.state.silence = SilenceMonitor(check_after=0.05, max_checks=0)
    call.state.silence.agent_finished(time.monotonic())
    task = asyncio.create_task(adapter._silence_loop(call.agent, call.state, call.log))
    await asyncio.sleep(1.6)
    task.cancel()
    assert call.state.hangup_reason == "no_response"
    assert provider.hangups == []  # the goodbye is still being spoken
    await call.event("response.done")
    await asyncio.sleep(0.5)
    assert provider.hangups == ["call-1"] and provider.outcomes == [(ORG, "no_response")]
    voice_decision.reset_flag_cache()


# --- recording consent tool --------------------------------------------------------------------------------------------------


async def test_consent_is_refused_for_a_call_that_did_not_ask_for_it() -> None:
    call = Call()
    await call.event("response.function_call_arguments.done", name="recording_consent", call_id="c1",
                     arguments=json.dumps({"granted": True}))
    assert call.outputs()[0]["reason"] == "consent_not_requested_for_this_call" and not call.state.recording_started


async def test_a_yes_starts_recording_a_no_or_an_unclear_answer_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    async def start(state: Any, log: Any) -> bool:
        started.append(state.call_uuid)
        return True

    async def no_db(*a: Any, **k: Any) -> None:
        return None

    from apps.api.channels.voice import compliance

    monkeypatch.setattr(call_control, "start_call_recording", start)
    monkeypatch.setattr(compliance, "record_consent_outcome", no_db)

    class _Ctx:
        async def __aenter__(self) -> Any:
            return None

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(adapter, "AsyncSessionLocal", lambda: _Ctx())

    declined = Call(consent_tool=True)
    await declined.event("response.function_call_arguments.done", name="recording_consent", call_id="c1",
                         arguments=json.dumps({"granted": False}))
    unclear = Call(consent_tool=True)
    await unclear.event("response.function_call_arguments.done", name="recording_consent", call_id="c2", arguments="{}")
    assert started == []  # no yes, no recording
    agreed = Call(consent_tool=True)
    await agreed.event("response.function_call_arguments.done", name="recording_consent", call_id="c3",
                       arguments=json.dumps({"granted": True}))
    await agreed.event("response.function_call_arguments.done", name="recording_consent", call_id="c4",
                       arguments=json.dumps({"granted": True}))
    assert started == ["call-1"] and agreed.state.recording_started  # and only once
    assert agreed.outputs()[0]["recording"] == "started"
