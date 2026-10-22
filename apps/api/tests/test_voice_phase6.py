"""Phase 6: final validation additions. Offline only: fake sockets, fake providers, synthetic data. Nothing here
touches a network, a phone or a paid API, and none of it proves behaviour on a real call.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from apps.api.channels.voice import adapter, call_verify, compliance, config_check, latency, metering, plivo_client, twilio_client
from apps.api.channels.voice.metering import CallUsage, flush_call_usage
from apps.api.config import settings
from apps.api.core.conversation.dialogue import looks_like_end_request
from apps.api.core.org_credentials import PlivoCredentials
from apps.api.db.models import Org, UsageEvent
from apps.api.db.models.conversation import Conversation
from apps.api.db.models.user import User

ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER = uuid.UUID("00000000-0000-0000-0000-000000000ccc")


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def info(self, event: str, *a: Any, **k: Any) -> None:
        self.events.append(event)

    warning = error = info


class _Ws:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail = fail

    async def send(self, raw: str) -> None:
        if self.fail:
            raise RuntimeError("socket gone")
        self.sent.append(json.loads(raw))


def _state(**fields: Any) -> adapter.CallState:
    state = adapter.CallState(user_id=USER, conversation_id=uuid.uuid4(), org_id=ORG, base_instructions="B", call_uuid="c1")
    for k, v in fields.items():
        setattr(state, k, v)
    return state


# --- end-of-call phrase detection (a false positive hangs up on a customer) -------------------------------------------------------

END_POSITIVE = [
    "phone rakh do", "please end the call", "call khatam karo", "ok thanks bye", "bye", "goodbye", "that's all",
    "bas ab", "ab bas", "theek hai, bye bye", "chalta hoon", "I have to go", "disconnect karo", "फोन रख दो", "bye bye, ab bas",
]
END_NEGATIVE = [
    "ab bas thoda time chahiye", "that's all fine, but what about the margin?", "bye the way, what is the price?",
    "bas ek minute", "I have to go through the paperwork first", "what is the goodbye fee", "call me tomorrow",
    "main office jaa raha hoon", "chalta hai, aage bataiye", "", "   ", "haan ji", "mujhe bas itna jaanna hai ki rate kya hai",
]


@pytest.mark.parametrize("text", END_POSITIVE)
def test_clear_end_requests_are_recognised(text: str) -> None:
    assert looks_like_end_request(text), text


@pytest.mark.parametrize("text", END_NEGATIVE)
def test_phrases_that_merely_contain_a_cue_do_not_end_the_call(text: str) -> None:
    assert not looks_like_end_request(text), text


# --- cross-path action identity ---------------------------------------------------------------------------------------------------


def test_the_same_booking_slot_is_one_action_whoever_asked() -> None:
    from_model = json.dumps({"date": "2026-10-20", "time": "15:00", "notes": "asked by caller"})
    from_decision = json.dumps({"time": "15:00", "date": "2026-10-20"})
    assert adapter._action_key("book_appointment", from_model) == adapter._action_key("book_appointment", from_decision)
    other = json.dumps({"date": "2026-10-20", "time": "16:00"})
    assert adapter._action_key("book_appointment", from_model) != adapter._action_key("book_appointment", other)
    assert adapter._action_key("book_appointment", "not json").startswith("book_appointment|")


async def test_a_booking_by_the_model_is_not_repeated_by_the_decision_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_reconnect_recovery", True)
    calls: list[str] = []

    async def fake_dispatch(name: str, args: str, *a: Any, **k: Any) -> dict[str, Any]:
        calls.append(name)
        return {"status": "ok"}

    monkeypatch.setattr(adapter, "_dispatch_realtime_tool", fake_dispatch)
    state, log = _state(), _Log()
    first = await adapter._dispatch_deduped(state, "book_appointment", json.dumps({"date": "d", "time": "t", "notes": "x"}), log)
    second = await adapter._dispatch_deduped(state, "book_appointment", json.dumps({"time": "t", "date": "d"}), log)
    assert calls == ["book_appointment"] and second.get("duplicate") is True and first["status"] == "ok"


# --- latency metrics --------------------------------------------------------------------------------------------------------------


def test_named_metrics_are_aggregated_with_percentiles_from_log_events() -> None:
    events = [{"event": "voice_metric", "metric": "tts_connect_ms", "value_ms": v} for v in (100, 200, 300, 400, 500)]
    events.append({"event": "voice_metric", "metric": "hangup_ms", "value_ms": 900})
    summary = latency.summarize_events(events)
    assert summary["tts_connect_ms"]["n"] == 5 and summary["tts_connect_ms"]["p50"] == 300
    assert summary["hangup_ms"]["n"] == 1


def test_metric_does_nothing_when_tracking_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_latency_tracking", False)
    before = latency.STATS.snapshot().get("probe_ms", {}).get("n", 0)
    latency.LatencyTracker(call_uuid="c1").metric("probe_ms", 5)
    assert latency.STATS.snapshot().get("probe_ms", {}).get("n", 0) == before


# --- interrupted audio and the model's context -----------------------------------------------------------------------------------


def test_heard_words_follow_the_share_of_audio_that_played(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_trim_interrupted_context", True)
    state = _state(resp_text="one two three four five six seven eight nine ten", resp_audio_seconds=10.0)
    state.playback_end = time.monotonic() + 5.0  # five of the ten seconds have not played yet
    adapter._snapshot_heard(state)
    assert state.heard_snapshot == "one two three four five"
    state.playback_end = time.monotonic() - 1  # everything played
    adapter._snapshot_heard(state)
    assert len(state.heard_snapshot.split()) == 10


def test_nothing_is_recorded_when_the_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_trim_interrupted_context", False)
    state = _state(resp_text="a b c", resp_audio_seconds=3.0)
    adapter._snapshot_heard(state)
    assert state.heard_snapshot is None


async def test_the_interrupted_answer_is_replaced_by_what_was_heard() -> None:
    ws, log = _Ws(), _Log()
    state = _state()
    state.response_items["r1"] = ["i1", "i2"]
    state.item_previous["i1"] = "prev"
    state.trim_pending["r1"] = "one two three"
    await adapter._trim_context(ws, state, log, "r1")
    types = [m["type"] for m in ws.sent]
    assert types == ["conversation.item.delete", "conversation.item.delete", "conversation.item.create"]
    assert ws.sent[2]["previous_item_id"] == "prev" and ws.sent[2]["item"]["content"][0]["text"] == "one two three"
    assert "r1" not in state.response_items and "r1" not in state.trim_pending


async def test_when_nothing_was_heard_the_answer_is_only_removed() -> None:
    ws = _Ws()
    state = _state()
    state.response_items["r1"] = ["i1"]
    state.item_previous["i1"] = "prev"
    state.trim_pending["r1"] = ""
    await adapter._trim_context(ws, state, _Log(), "r1")
    assert [m["type"] for m in ws.sent] == ["conversation.item.delete"]


async def test_without_a_known_position_the_answer_is_removed_not_reinserted_out_of_order() -> None:
    ws = _Ws()
    state = _state()
    state.response_items["r1"] = ["i1"]
    state.trim_pending["r1"] = "some words"
    await adapter._trim_context(ws, state, _Log(), "r1")
    assert [m["type"] for m in ws.sent] == ["conversation.item.delete"]


async def test_a_failure_while_trimming_never_raises() -> None:
    state, log = _state(), _Log()
    state.response_items["r1"] = ["i1"]
    await adapter._trim_context(_Ws(fail=True), state, log, "r1")
    assert "voice_context_trim_failed" in log.events


# --- checkpointed usage -----------------------------------------------------------------------------------------------------------


@pytest.fixture
async def ledger(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    db_session.add_all([Org(id=ORG, name="A"), Org(id=OTHER, name="B")])
    await db_session.commit()
    monkeypatch.setattr(settings, "voice_metering_v2", True)
    return db_session


async def _sum(db: Any, usage_type: str) -> float:
    rows = (await db.execute(select(UsageEvent).where(UsageEvent.usage_type == usage_type))).scalars().all()
    return float(sum(r.quantity for r in rows))


async def _flush(db: Any, usage: CallUsage, seconds: float | None = None) -> list[str]:
    written = await flush_call_usage(db, org_id=ORG, call_uuid="call-1", provider="plivo", usage=usage, call_seconds=seconds)
    await db.commit()
    usage.confirm()
    return written


async def test_checkpoints_write_only_what_is_new_and_add_up_to_the_total(ledger: Any) -> None:
    usage = CallUsage()
    usage.add_tts_chars(100)
    await _flush(ledger, usage, 60)
    usage.add_tts_chars(40)
    await _flush(ledger, usage, 120)
    usage.add_tts_chars(10)
    await _flush(ledger, usage, 150)
    assert await _sum(ledger, "tts_characters") == 150
    assert await _sum(ledger, "voice_seconds") == 150


async def test_a_checkpoint_with_nothing_new_writes_nothing(ledger: Any) -> None:
    usage = CallUsage()
    usage.add_tts_chars(100)
    await _flush(ledger, usage)
    assert await _flush(ledger, usage) == []
    assert await _sum(ledger, "tts_characters") == 100


async def test_a_failed_checkpoint_is_covered_by_the_next_one_without_double_counting(ledger: Any) -> None:
    usage = CallUsage()
    usage.add_tts_chars(100)
    usage._pending = {"tts": 100}  # a checkpoint was prepared but failed before anything was written or confirmed
    usage._pending = {}  # (what checkpoint_call_usage does on failure)
    usage.add_tts_chars(20)
    await _flush(ledger, usage)
    assert await _sum(ledger, "tts_characters") == 120


async def test_a_crash_after_a_checkpoint_keeps_what_was_checkpointed(ledger: Any) -> None:
    usage = CallUsage()
    usage.add_tts_chars(100)
    await _flush(ledger, usage, 60)
    usage.add_tts_chars(500)  # the process dies here: this part is lost, the earlier part is not
    assert await _sum(ledger, "tts_characters") == 100 and await _sum(ledger, "voice_seconds") == 60


async def test_the_first_checkpoint_keeps_the_original_event_ids(ledger: Any) -> None:
    usage = CallUsage()
    usage.add_tts_chars(100)
    await _flush(ledger, usage)
    row = (await ledger.execute(select(UsageEvent).where(UsageEvent.usage_type == "tts_characters"))).scalar_one()
    assert str(row.event_id) == str(metering.deterministic_event_id("voice_usage", "tts", "call-1"))


async def test_the_checkpoint_helper_never_raises_and_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom:
        def __call__(self) -> Any:
            raise RuntimeError("db down")

    ok = await metering.checkpoint_call_usage(Boom(), org_id=ORG, call_uuid="c", provider="plivo", usage=CallUsage(), call_seconds=5)
    assert ok is False


# --- provider-hosted recording deletion ------------------------------------------------------------------------------------------


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://media.plivo.com/Recording/abc-123.mp3", ("plivo", "abc-123")),
        ("https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE9.mp3", ("twilio", "RE9")),
        ("", None),
        (None, None),
    ],
)
def test_the_provider_and_recording_id_are_read_from_the_stored_url(url: str | None, expected: Any) -> None:
    assert compliance.recording_provider_and_id(url) == expected


@pytest.mark.parametrize(("status", "expected"), [(204, "ok"), (404, "already_gone"), (410, "already_gone"), (500, "failed"), (401, "failed")])
async def test_plivo_recording_deletion_outcomes(monkeypatch: pytest.MonkeyPatch, status: int, expected: str) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status)

    monkeypatch.setattr(plivo_client, "_http", _http(handler))
    result = await plivo_client.delete_recording(PlivoCredentials(auth_id="AID", auth_token="t"), "rec1")
    assert result == expected and seen[0].method == "DELETE" and "/Recording/rec1/" in str(seen[0].url)


async def test_a_network_error_is_a_failure_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(plivo_client, "_http", _http(boom))
    assert await plivo_client.delete_recording(PlivoCredentials(auth_id="AID", auth_token="t"), "r") == "failed"
    assert hasattr(twilio_client, "delete_recording")


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
async def retention(db_session: Any) -> Any:
    db_session.add_all([Org(id=ORG, name="A", voice_compliance={"retention_days": 30}), Org(id=OTHER, name="B")])
    db_session.add_all([User(id=USER, org_id=ORG, phone="+91000000001")])
    await db_session.commit()
    return db_session


async def _old(db: Any, url: str = "https://media.plivo.com/Recording/r1.mp3") -> Conversation:
    conversation = Conversation(
        org_id=ORG, user_id=USER, channel="voice", recording_url=url, recording_duration_secs=9.0, ended_at=NOW - timedelta(days=90)
    )
    db.add(conversation)
    await db.commit()
    return conversation


async def test_the_reference_is_cleared_only_after_the_provider_confirms_deletion(retention: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    done, kept = await _old(retention), await _old(retention, "https://media.plivo.com/Recording/r2.mp3")
    outcomes = {"r1": "ok", "r2": "failed"}

    async def fake(org: Any, url: str | None) -> str:
        return outcomes[url.rsplit("/", 1)[-1].split(".")[0]]

    monkeypatch.setattr(compliance, "_delete_provider_audio", fake)
    cleared = await compliance.purge_expired_recordings(retention, now=NOW, delete_provider_audio=True)
    await retention.refresh(done)
    await retention.refresh(kept)
    assert cleared == 1 and done.recording_url is None and kept.recording_url  # retried next run


async def test_an_already_deleted_recording_counts_as_deleted(retention: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    conversation = await _old(retention)

    async def fake(org: Any, url: str | None) -> str:
        return "already_gone"

    monkeypatch.setattr(compliance, "_delete_provider_audio", fake)
    assert await compliance.purge_expired_recordings(retention, now=NOW, delete_provider_audio=True) == 1
    await retention.refresh(conversation)
    assert conversation.recording_url is None


async def test_without_credentials_the_reference_is_kept(retention: Any) -> None:
    conversation = await _old(retention)
    assert await compliance.purge_expired_recordings(retention, now=NOW, delete_provider_audio=True) in (0, 1)
    await retention.refresh(conversation)
    # No org Plivo credentials exist in the test environment, so nothing may be claimed deleted.
    if settings.plivo_auth_id:
        pytest.skip("platform Plivo credentials present")
    assert conversation.recording_url is not None


async def test_the_default_path_does_not_contact_the_provider(retention: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    conversation = await _old(retention)

    async def forbidden(org: Any, url: str | None) -> str:
        raise AssertionError("provider contacted")

    monkeypatch.setattr(compliance, "_delete_provider_audio", forbidden)
    assert settings.voice_retention_delete_provider_audio is False
    assert await compliance.purge_expired_recordings(retention, now=NOW) == 1
    await retention.refresh(conversation)
    assert conversation.recording_url is None


# --- configuration checks ---------------------------------------------------------------------------------------------------------


def _cfg(**over: Any) -> SimpleNamespace:
    base = dict(
        voice_tts_provider="elevenlabs", elevenlabs_api_key="k", public_base_url="https://app.test", openai_api_key="o",
        voice_interruption_mode="answer_now", voice_live_turn_detection=True, voice_usage_checkpoint_seconds=60,
        voice_tts_first_audio_timeout_seconds=6.0, voice_end_call_max_wait_seconds=8,
        # Phase 9: a sane deployment authenticates its webhooks and bounds its calls.
        environment="production", plivo_signature_mode="enforce", plivo_auth_token="t",
        voice_max_active_streams=30, voice_max_call_seconds=1800, voice_hangup_on_stream_end=True,
    )
    base.update({name: False for name in config_check.BEHAVIOUR_FLAGS})
    base["log_privacy_enabled"] = True
    base.update(over)
    return SimpleNamespace(**base)


def _codes(**over: Any) -> set[str]:
    return {i.code for i in config_check.check_voice_config(_cfg(**over))}


def test_a_sane_configuration_has_no_issues() -> None:
    assert _codes() == set()


def test_elevenlabs_without_a_key_is_an_error() -> None:
    issues = config_check.check_voice_config(_cfg(elevenlabs_api_key=None))
    assert [(i.level, i.code) for i in issues] == [("error", "elevenlabs_key_missing")]


def test_elevenlabs_only_flags_warn_on_the_openai_voice() -> None:
    codes = _codes(voice_tts_provider="openai", voice_tts_recovery=True, voice_elevenlabs_prewarm=True)
    assert {"voice_tts_recovery_no_effect", "voice_elevenlabs_prewarm_no_effect"} <= codes


def test_flag_interactions_are_reported() -> None:
    assert "safe_cancel_finish_first" in _codes(voice_safe_cancel=True, voice_interruption_mode="finish_first")
    assert "smart_turns_without_live_detection" in _codes(voice_smart_turn_taking=True, voice_live_turn_detection=False)
    assert "provider_delete_without_worker" in _codes(voice_retention_delete_provider_audio=True)
    assert "tts_timeout_aggressive" in _codes(voice_tts_recovery=True, voice_tts_first_audio_timeout_seconds=1.0)
    assert "log_privacy_off" in _codes(log_privacy_enabled=False)
    assert "public_base_url_default" in _codes(public_base_url="https://example.com")


def test_the_real_defaults_have_every_behaviour_flag_off_except_the_log_hygiene_ones() -> None:
    snapshot = config_check.flag_snapshot()
    on = {k for k, v in snapshot.items() if v}
    assert on <= {"log_privacy_enabled", "log_transcripts", "voice_latency_tracking", "voice_greeting_ai_disclosure"}, on


async def test_the_voice_config_endpoint_needs_the_admin_token(client: Any) -> None:
    assert (await client.get("/diag/voice-config")).status_code == 403
    ok = await client.get("/diag/voice-config", headers={"X-Admin-Token": settings.admin_token})
    assert ok.status_code == 200 and "flags" in ok.json() and "issues" in ok.json()


# --- post-call verification -------------------------------------------------------------------------------------------------------


def _by_id(results: list[call_verify.CheckResult]) -> dict[str, str]:
    return {r.id: r.status for r in results}


def test_a_clean_synthetic_call_passes_and_listening_checks_stay_manual() -> None:
    events = [
        {"event": "voice_stream_connected"}, {"event": "voice_turn_latency", "total_ms": 900},
        {"event": "voice_turn_latency", "total_ms": 1100}, {"event": "voice_stream_ended"},
    ]
    conv = {"messages": [{"role": "assistant", "content": "Namaste, main Veerox ki AI assistant bol rahi hoon."}]}
    result = _by_id(call_verify.verify_call(events, ["connected", "no_errors", "first_audio_latency", "greeting_disclosure", "hindi_naturalness"], conversation=conv))
    assert result == {"connected": "pass", "no_errors": "pass", "first_audio_latency": "pass", "greeting_disclosure": "pass", "hindi_naturalness": "manual"}


def test_errors_and_slow_audio_fail() -> None:
    events = [{"event": "voice_stream_connected"}, {"event": "voice_stream_ended"}, {"event": "voice_stream_error"},
              {"event": "voice_turn_latency", "total_ms": 9000}]
    result = _by_id(call_verify.verify_call(events, ["no_errors", "first_audio_latency"]))
    assert result == {"no_errors": "fail", "first_audio_latency": "fail"}


def test_missing_data_is_skipped_never_passed() -> None:
    result = _by_id(call_verify.verify_call([], ["connected", "usage_recorded", "privacy_logs", "appointment_truthful", "bogus"]))
    assert result["connected"] == "skipped" and result["usage_recorded"] == "skipped" and result["privacy_logs"] == "skipped"
    assert result["appointment_truthful"] == "skipped" and result["bogus"] == "fail"


def test_a_claimed_booking_without_a_record_fails_and_a_duplicate_fails() -> None:
    conv = {"messages": [{"role": "assistant", "content": "Aapki appointment confirmed hai."}]}
    assert _by_id(call_verify.verify_call([], ["appointment_truthful"], conversation=conv, appointments=[]))["appointment_truthful"] == "fail"
    dup = [{"date": "d", "time": "t"}, {"date": "d", "time": "t"}]
    assert _by_id(call_verify.verify_call([], ["appointment_truthful", "no_duplicate_actions"], conversation=conv, appointments=dup)) == {
        "appointment_truthful": "fail", "no_duplicate_actions": "fail"}


def test_a_hangup_the_provider_did_not_confirm_fails() -> None:
    events = [{"event": "voice_call_hangup", "result": "failed"}, {"event": "voice_hangup_failed_line_may_be_open"}]
    assert _by_id(call_verify.verify_call(events, ["call_ended_cleanly"]))["call_ended_cleanly"] == "fail"
    ok = [{"event": "voice_call_hangup", "result": "ok"}]
    conv = {"messages": [{"role": "assistant", "content": "bye"}], "tags": ["outcome:completed"]}
    assert _by_id(call_verify.verify_call(ok, ["call_ended_cleanly", "goodbye_before_hangup"], conversation=conv)) == {
        "call_ended_cleanly": "pass", "goodbye_before_hangup": "pass"}


def test_usage_checks_catch_duplicates_and_unmeasured_rows() -> None:
    rows = [{"event_id": "a", "usage_type": "voice_seconds", "quantity": 60, "metadata": {"basis": "measured"}}]
    assert _by_id(call_verify.verify_call([], ["usage_recorded"], usage_rows=rows, conversation={"duration_seconds": 62}))["usage_recorded"] == "pass"
    assert _by_id(call_verify.verify_call([], ["usage_recorded"], usage_rows=rows + rows))["usage_recorded"] == "fail"
    assert _by_id(call_verify.verify_call([], ["usage_recorded"], usage_rows=rows, conversation={"duration_seconds": 300}))["usage_recorded"] == "fail"
    est = [{"event_id": "b", "usage_type": "voice_seconds", "quantity": 5, "metadata": {"basis": "estimated"}}]
    assert _by_id(call_verify.verify_call([], ["usage_recorded"], usage_rows=est))["usage_recorded"] == "fail"


def test_unmasked_phone_numbers_in_logs_are_flagged() -> None:
    assert _by_id(call_verify.verify_call([], ["privacy_logs"], raw_lines="to=+919876543210 ok"))["privacy_logs"] == "fail"
    assert _by_id(call_verify.verify_call([], ["privacy_logs"], raw_lines="to=+91*****3210 ok"))["privacy_logs"] == "pass"


def test_recording_policy_check_compares_with_the_expectation() -> None:
    conv = {"recording_url": None}
    assert _by_id(call_verify.verify_call([], ["recording_policy"], conversation=conv, params={"expect_recording": False}))["recording_policy"] == "pass"
    assert _by_id(call_verify.verify_call([], ["recording_policy"], conversation=conv, params={"expect_recording": True}))["recording_policy"] == "fail"
