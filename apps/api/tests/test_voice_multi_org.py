"""Phase 5: multi-organization safety, configuration compatibility and interactions with the
Phase 1-4 features.

Part of the job is documenting what stops this service scaling horizontally: any state that
lives in one process's memory. ``test_process_local_state_is_exactly_what_we_know_about`` fails
when a new such store appears, so the list in the report cannot silently go stale.
"""

from __future__ import annotations

import ast
import asyncio
import collections
import importlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from apps.api.channels.voice import adapter, call_control, elevenlabs_client, recovery
from apps.api.channels.voice import realtime_bridge as bridge
from apps.api.channels.voice.greeting import GreetingAudioCache
from apps.api.config import Settings, settings
from apps.api.core.tools import TOOL_DEFINITIONS
from apps.api.db.models import Org
from apps.api.tests.test_voice_tts_recovery import Call as RecoveryCall
from apps.api.tests.test_voice_tts_recovery import RSession

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER = uuid.UUID("00000000-0000-0000-0000-000000000ccc")
MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations" / "versions"


# --- calls never share state -----------------------------------------------------------------------------------------------


def _state(org: uuid.UUID) -> adapter.CallState:
    return adapter.CallState(user_id=USER, conversation_id=uuid.uuid4(), org_id=org, call_uuid=f"call-{org.int}")


def test_two_calls_share_no_mutable_state() -> None:
    a, b = _state(ORG_A), _state(ORG_B)
    for name in ("cancelled_response_ids", "action_results", "completed_actions", "overlap_transcripts",
                 "voice_actions_done", "usage", "latency"):
        assert getattr(a, name) is not getattr(b, name), name
    a.cancelled_response_ids.add("r1")
    a.action_results["book|x"] = {"status": "ok"}
    a.completed_actions.append("book_appointment")
    a.usage.add_tts_chars(100)
    assert not b.cancelled_response_ids and not b.action_results and not b.completed_actions and b.usage.tts_chars == 0


async def test_simultaneous_hangups_for_different_orgs_stay_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    ended: list[tuple[str, uuid.UUID]] = []
    outcomes: list[tuple[uuid.UUID, str]] = []

    async def hangup(state: Any, log: Any) -> str:
        ended.append((state.call_uuid, state.org_id))
        return "ok"

    async def outcome(conversation_id: Any, org_id: uuid.UUID, name: str) -> None:
        outcomes.append((org_id, name))

    class _Log:
        def info(self, *a: Any, **k: Any) -> None:
            return None

        warning = info

    monkeypatch.setattr(call_control, "provider_hangup", hangup)
    monkeypatch.setattr(call_control, "record_call_outcome", outcome)
    monkeypatch.setattr(settings, "voice_end_call_grace_seconds", 0.0)
    a, b = _state(ORG_A), _state(ORG_B)
    ta = call_control.request_hangup(a, _Log(), reason="task_complete", outcome="completed")
    tb = call_control.request_hangup(b, _Log(), reason="customer_requested", outcome="customer_requested_end", abortable=False)
    await asyncio.gather(ta, tb)
    assert sorted(ended) == sorted([(a.call_uuid, ORG_A), (b.call_uuid, ORG_B)])
    assert sorted(outcomes, key=str) == sorted([(ORG_A, "completed"), (ORG_B, "customer_requested_end")], key=str)
    assert a.hangup_done and b.hangup_done


# --- caches are keyed by org / voice -------------------------------------------------------------------------------------------


def test_the_greeting_cache_never_serves_one_orgs_audio_to_another() -> None:
    assert GreetingAudioCache.key(ORG_A, "Namaste") != GreetingAudioCache.key(ORG_B, "Namaste")
    assert GreetingAudioCache.key(ORG_A, "Namaste") == GreetingAudioCache.key(ORG_A, "Namaste")


# --- what lives in one process's memory (the horizontal-scaling inventory) ------------------------------------------------------


# module -> the stores that exist only in that process, and why that matters when scaling out.
PROCESS_LOCAL = {
    "apps.api.channels.voice.realtime_bridge": {
        "_pending_precalls",       # a pre-connected OpenAI session: the media stream must reach the SAME process
        "_pending_precall_orgs",   # which org it was prepared for (checked on claim)
        "greeting_cache",          # greeting audio; a miss just means synthesizing again
    },
    "apps.api.channels.voice.greeting": {"greeting_cache"},
    "apps.api.channels.voice.recovery": {"fallback_audio"},       # apology audio; a miss means no spoken apology
    "apps.api.channels.voice.voice_preview": {"preview_limiter"},  # preview rate limit is per process (approximate)
    "apps.api.channels.voice.latency": {"STATS"},                  # diagnostics only
    "apps.api.core.conversation.voice_decision": {"_mode_cache", "_smart_cache"},  # 30 s TTL; staleness only
    "apps.api.core.conversation.config": {"_active_cache", "_version_cache", "_pin_cache"},  # TTL / immutable
    "apps.api.core.conversation.runtime_stats": {"_latencies", "_outcomes"},        # diagnostics only
}
_CONSTANTS = {"DISPATCH_TABLE", "TOOL_DEFINITIONS", "END_CALL_TOOL", "RECORDING_CONSENT_TOOL", "MEASURED",
              "_LANGUAGE_NAME", "_DEFAULT_BY_ID", "_TOPIC_PATTERNS", "_DISPOSITION_RANK", "_INTERNAL_MEMORY_KEYS"}
_STORE_TYPES = ("GreetingAudioCache", "FallbackAudioCache", "RollingStats", "RateLimiter")


def test_process_local_state_is_exactly_what_we_know_about() -> None:
    found: dict[str, set[str]] = {}
    for module_name in PROCESS_LOCAL:
        module = importlib.import_module(module_name)
        names = set()
        for key, value in vars(module).items():
            if key.startswith("__") or key in _CONSTANTS:
                continue
            if isinstance(value, (dict, list, set, collections.deque, collections.OrderedDict, collections.Counter)) \
                    or type(value).__name__ in _STORE_TYPES:
                names.add(key)
        found[module_name] = names
    assert found == PROCESS_LOCAL, (
        "A new in-memory store appeared (or one went away). Decide whether it breaks running more than one "
        "instance, then add it to PROCESS_LOCAL with the reason."
    )


# --- configuration compatibility ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag",
    ["voice_end_call_enabled", "voice_tts_recovery", "voice_reconnect_recovery", "voice_metering_v2",
     "voice_retention_worker_enabled", "voice_safe_cancel", "voice_smart_turn_taking", "voice_elevenlabs_prewarm",
     "voice_nonblocking_tts_finish", "voice_greeting_audio_cache"],
)
def test_every_behaviour_change_ships_switched_off(flag: str) -> None:
    assert Settings.model_fields[flag].default is False


def test_log_hygiene_ships_on_but_keeps_transcript_logging() -> None:
    assert Settings.model_fields["log_privacy_enabled"].default is True
    assert Settings.model_fields["log_transcripts"].default is True  # unchanged unless an operator turns it off


def test_new_orgs_default_brand_is_veerox_and_the_legacy_brand_is_untouched() -> None:
    assert Settings.model_fields["voice_new_org_brand"].default == "Veerox"
    assert Settings.model_fields["voice_brand_name"].default == "WorkAssign AI"


def test_the_tool_list_is_exactly_the_shared_one_until_a_feature_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("voice_end_call_enabled",):
        monkeypatch.setattr(settings, name, False)
    shared = [d["function"]["name"] for d in TOOL_DEFINITIONS]
    assert [t["name"] for t in adapter.realtime_tools()] == shared
    assert [t["name"] for t in bridge._session_update_event("x")["session"]["tools"]] == shared


def test_new_org_columns_are_empty_for_every_existing_org() -> None:
    org = Org(name="legacy")
    assert org.voice_brand is None and org.voice_compliance is None and org.voice_greeting is None and org.voice_profile is None


def test_the_migration_chain_has_one_head_and_the_new_migration_is_additive() -> None:
    sources = {p.name: p.read_text(encoding="utf8") for p in MIGRATIONS.glob("*.py")}
    revisions: dict[str, str | None] = {}
    for text in sources.values():
        tree = ast.parse(text)
        rev, down = None, None
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                if isinstance(target, ast.Name) and target.id == "revision":
                    rev = ast.literal_eval(node.value)
                if isinstance(target, ast.Name) and target.id == "down_revision":
                    down = ast.literal_eval(node.value)
        if rev:
            revisions[rev] = down
    heads = set(revisions) - {d for d in revisions.values() if d}
    assert heads == {"a7c9e1f3b5d2"} and revisions["a7c9e1f3b5d2"] == "f3b5d7e9a1c2"

    text = next(t for n, t in sources.items() if n.startswith("a7c9e1f3b5d2"))
    tree = ast.parse(text)
    funcs = {
        f.name: [ast.unparse(stmt) for stmt in f.body if isinstance(stmt, ast.Expr) and not isinstance(stmt.value, ast.Constant)]
        for f in tree.body
        if isinstance(f, ast.FunctionDef)
    }
    assert len(funcs["upgrade"]) == 2
    assert all(c.startswith("op.add_column(") and "nullable=True" in c for c in funcs["upgrade"])  # additive, nullable
    assert len(funcs["downgrade"]) == 2 and all(c.startswith("op.drop_column(") for c in funcs["downgrade"])


# --- bad configuration degrades safely -------------------------------------------------------------------------------------------


async def test_nonsense_timeouts_cannot_crash_or_unbound_the_tts_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_tts_recovery", True)
    for junk in ("soon", None, float("nan"), -1):
        monkeypatch.setattr(settings, "voice_tts_first_audio_timeout_seconds", junk)
        call = RecoveryCall()
        call.state.eleven_session = SimpleNamespace()
        adapter._start_tts_watchdog(call.state, call.log)
        assert call.state.tts_watchdog_task is not None
        call.state.tts_watchdog_task.cancel()
    monkeypatch.setattr(settings, "voice_tts_max_failures_per_call", None)
    assert int(call_control.bounded(settings.voice_tts_max_failures_per_call, 1, 10, 2)) == 2


# --- the new flags together with Phase 1-4 --------------------------------------------------------------------------------------


@pytest.fixture
def everything_on(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    async def no_synth(text: str, voice: Any = None) -> list[str]:
        return []

    seen = SimpleNamespace(hangups=[], persisted=[], tools=[])

    async def hangup(state: Any, log: Any) -> str:
        seen.hangups.append(state.call_uuid)
        return "ok"

    async def noop(*a: Any, **k: Any) -> None:
        return None

    async def persist(state: Any, text: str, *, pending: Any = None) -> None:
        seen.persisted.append(text)

    async def tool(name: str, args_json: str, *a: Any, **k: Any) -> dict[str, Any]:
        seen.tools.append(name)
        return {"status": "ok"}

    RSession.instances, RSession.script = [], []
    monkeypatch.setattr(elevenlabs_client, "ElevenLabsTTSSession", RSession)
    monkeypatch.setattr(elevenlabs_client, "is_configured", lambda: True)
    monkeypatch.setattr(recovery, "synthesize_greeting", no_synth)
    monkeypatch.setattr(call_control, "provider_hangup", hangup)
    monkeypatch.setattr(call_control, "record_call_outcome", noop)
    monkeypatch.setattr(adapter, "_persist_voice_turn", persist)
    monkeypatch.setattr(adapter, "_dispatch_realtime_tool", tool)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    for name in ("voice_end_call_enabled", "voice_tts_recovery", "voice_reconnect_recovery", "voice_metering_v2",
                 "voice_safe_cancel", "voice_smart_turn_taking", "voice_elevenlabs_prewarm",
                 "voice_nonblocking_tts_finish", "voice_latency_tracking"):
        monkeypatch.setattr(settings, name, True)
    monkeypatch.setattr(settings, "voice_end_call_grace_seconds", 0.0)
    monkeypatch.setattr(settings, "voice_tts_first_audio_timeout_seconds", 30.0)
    return seen


async def test_a_normal_turn_is_unaffected_with_every_feature_enabled(everything_on: SimpleNamespace) -> None:
    call = RecoveryCall()
    state = call.state
    await call.event("response.created", response={"id": "r1"})
    await asyncio.sleep(0.02)  # prewarm
    await call.event("response.text.delta", delta="Namaste ", response_id="r1")
    await call.event("response.text.delta", delta="ji", response_id="r1")
    await call.event("response.text.done", text="Namaste ji", response_id="r1")
    await asyncio.wait_for(state.eleven_finalize_task, timeout=2)
    await call.event("response.done", response={"id": "r1", "usage": {"input_tokens": 50, "output_tokens": 10}})

    assert call.audio() == ["QUJD"]                          # heard exactly once
    assert everything_on.persisted == ["Namaste ji"]          # recorded exactly once
    assert state.tts_failures == 0 and not state.tts_failed_over and state.hangup_task is None
    assert state.usage.tts_chars == len("Namaste ji") and state.usage.realtime_input_tokens == 50
    assert len(RSession.instances) == 1 and RSession.instances[0].closed  # one socket, closed
    await adapter.cancel_pending_tasks(state)


async def test_an_interruption_after_a_recovered_turn_stops_everything(everything_on: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    # With prewarm on, the first failure is absorbed by the prewarm (its error is swallowed and the first text
    # delta opens its own socket), so it takes two failed opens before the turn actually needs recovering.
    RSession.script = [{"open_error": OSError("blip")}, {"open_error": OSError("blip")}, {"audio": ["QQ=="]}]
    call = RecoveryCall()
    call.state.live = None
    await call.event("response.created", response={"id": "r1"})
    await call.event("response.text.delta", delta="Pehla jawab", response_id="r1")
    await call.settle()
    assert call.state.tts_failures == 1 and call.audio() == ["QQ=="]
    call.state.playback_end = time.monotonic() + 5

    await adapter._cut_now(_Agent(), call.phone, call.state, call.log, "test")  # type: ignore[arg-type]

    opened = [s for s in RSession.instances if not s.behaviour.get("open_error")]  # a socket that never opened has nothing to close
    assert call.cleared() and opened and all(s.closed for s in opened)
    assert call.state.eleven_session is None
    await call.event("response.text.delta", delta="stale text", response_id="r1")  # a cancelled response's late text
    assert len(RSession.instances) == 3 and everything_on.hangups == []  # no new socket, no hangup
    await adapter.cancel_pending_tasks(call.state)


class _Agent:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))
