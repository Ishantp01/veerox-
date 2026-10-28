# ruff: noqa: E501  (multilingual transcripts; long check descriptions)
"""Phase 5 readiness: every experimental flag is off by default, the baseline session is unchanged, flag combinations
that cannot work are reported, and the post-call checks for the Phase 3-4 behaviours decide correctly on synthetic data.
Pure and offline: no network, no database, no phone."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from apps.api.channels.voice import call_verify, config_check, realtime_bridge
from apps.api.config import Settings

ROOT = Path(__file__).resolve().parents[3]

# Every behaviour flag that is ON by default today (pre-existing, unchanged by Phases 3-5). Anything new must be off.
DEFAULT_ON = {
    "conversation_voice_live", "conversation_voice_shadow", "log_privacy_enabled", "log_transcripts",
    "plivo_startup_registration", "sarvam_fallback_to_elevenlabs", "voice_greeting_ai_disclosure",
    "voice_latency_tracking", "voice_live_turn_detection",
}
NEW_FLAGS = (
    "voice_tts_phrase_flush", "voice_script_authority", "voice_exact_lines", "voice_if_triggers",
    "voice_conversation_memory", "voice_opener_variety", "voice_adaptive_endpointing",
)


def _bool_defaults() -> dict[str, bool]:
    prefixes = ("voice_", "conversation_", "sarvam_", "log_", "plivo_", "pilot_")
    return {
        n: bool(f.default) for n, f in Settings.model_fields.items() if f.annotation is bool and n.startswith(prefixes)
    }


def test_no_flag_added_in_phases_3_to_5_is_on_by_default() -> None:
    defaults = _bool_defaults()
    for flag in NEW_FLAGS:
        assert defaults[flag] is False, flag


def test_the_only_behaviour_flags_that_default_on_are_the_ones_already_on_before() -> None:
    assert {n for n, v in _bool_defaults().items() if v} == DEFAULT_ON


def test_the_numeric_knobs_default_to_the_baseline_timing() -> None:
    fields = Settings.model_fields
    assert fields["voice_vad_silence_ms"].default == 1100
    assert fields["voice_adaptive_vad_silence_ms"].default == 700 and fields["voice_adaptive_settle_ms"].default == 250


def test_every_new_flag_is_visible_to_the_voice_config_diagnostic() -> None:
    for flag in NEW_FLAGS + ("voice_human_style",):
        assert flag in config_check.BEHAVIOUR_FLAGS, flag
    snapshot = config_check.flag_snapshot(SimpleNamespace())  # an empty settings object: everything reads as off
    assert not any(snapshot[f] for f in NEW_FLAGS)


def test_the_baseline_realtime_session_is_exactly_what_it_was(monkeypatch) -> None:  # noqa: ANN001
    """All Phase 3-5 flags off: the turn-detection block of the session the model receives is unchanged."""
    from apps.api.config import settings

    for flag in NEW_FLAGS:
        monkeypatch.setattr(settings, flag, False)
    monkeypatch.setattr(settings, "voice_vad_silence_ms", 1100)
    detection = realtime_bridge._session_update_event("x")["session"]["audio"]["input"]["turn_detection"]
    assert detection == {
        "type": "server_vad", "threshold": 0.5, "prefix_padding_ms": 300, "silence_duration_ms": 1100,
        "interrupt_response": False, "create_response": False,
    }


# --- flag combinations ------------------------------------------------------------------------------------------


def _codes(**flags: object) -> set[str]:
    base = {"voice_tts_provider": "elevenlabs", "elevenlabs_api_key": "k", "openai_api_key": "k",
            "voice_live_turn_detection": True, "plivo_auth_token": "t", "public_base_url": "https://x.example.org"}
    return {i.code for i in config_check.check_voice_config(SimpleNamespace(**{**base, **flags}))}


def test_flags_that_depend_on_script_authority_say_so_when_it_is_off() -> None:
    codes = _codes(voice_exact_lines=True, voice_if_triggers=True, voice_human_style=True)
    assert {"exact_lines_without_authority", "if_triggers_without_authority", "human_style_overrides_scripts"} <= codes
    assert not _codes(voice_exact_lines=True, voice_if_triggers=True, voice_script_authority=True) & {
        "exact_lines_without_authority", "if_triggers_without_authority"
    }


def test_exact_lines_on_native_audio_are_reported_as_prompt_only() -> None:
    assert "exact_lines_prompt_only" in _codes(voice_exact_lines=True, voice_script_authority=True, voice_tts_provider="openai")
    assert "exact_lines_prompt_only" not in _codes(voice_exact_lines=True, voice_script_authority=True)


def test_adaptive_endpointing_without_live_text_or_without_headroom_is_reported() -> None:
    assert "adaptive_without_live_detection" in _codes(voice_adaptive_endpointing=True, voice_live_turn_detection=False)
    assert "adaptive_no_speedup" in _codes(
        voice_adaptive_endpointing=True, voice_adaptive_vad_silence_ms=700, voice_vad_silence_ms=650
    )
    assert "adaptive_replaces_hold" in _codes(voice_adaptive_endpointing=True, voice_smart_turn_taking=True)
    assert not _codes() & {"adaptive_without_live_detection", "adaptive_no_speedup", "adaptive_replaces_hold"}


def test_phrase_flush_on_native_audio_has_no_effect() -> None:
    assert "voice_tts_phrase_flush_no_effect" in _codes(voice_tts_phrase_flush=True, voice_tts_provider="openai")


# --- post-call checks ----------------------------------------------------------------------------------------------

LINE = "Yeh call quality aur training ke liye record ho sakti hai."


def _conv(*assistant: str) -> dict:
    return {"messages": [{"role": "assistant", "content": t} for t in assistant]}


def _run(check: str, events=None, conversation=None, params=None) -> call_verify.CheckResult:  # noqa: ANN001
    return call_verify.verify_call(events if events is not None else [], [check], conversation=conversation, params=params)[0]


def test_exact_line_spoken_word_for_word_passes() -> None:
    r = _run("exact_lines_verbatim", conversation=_conv("Namaste.", f"  {LINE} "), params={"exact_lines": [LINE]})
    assert r.status == "pass"


def test_a_paraphrased_exact_line_fails_without_quoting_it() -> None:
    r = _run("exact_lines_verbatim", conversation=_conv("Yeh call record ho sakti hai."), params={"exact_lines": [LINE]})
    assert r.status == "fail" and "Yeh call" not in r.detail


def test_a_lead_in_before_an_exact_line_fails() -> None:
    r = _run("exact_lines_verbatim", conversation=_conv(f"Haan ji, {LINE}"), params={"exact_lines": [LINE]})
    assert r.status == "fail" and "lead-in" in r.detail
    assert _run("exact_lines_verbatim", conversation=_conv(f"Aapka din achha rahe. {LINE}"), params={"exact_lines": [LINE]}).status == "pass"


def test_exact_line_check_is_skipped_without_its_inputs() -> None:
    assert _run("exact_lines_verbatim", params={"exact_lines": [LINE]}).status == "skipped"
    assert _run("exact_lines_verbatim", conversation=_conv("x")).status == "skipped"


def test_repeated_questions_are_counted_only_when_memory_was_active() -> None:
    quiet = [{"event": "voice_turn_event", "kind": "repeat_question"}]
    assert _run("no_repeat_questions", events=quiet).status == "skipped"  # no memory updates logged: cannot tell
    active = [{"event": "voice_turn_trace", "kind": "memory_updated"}]
    assert _run("no_repeat_questions", events=active).status == "pass"
    assert _run("no_repeat_questions", events=active + quiet).status == "fail"


def test_keyword_rule_firing_is_checked_against_the_expected_count() -> None:
    fired = [{"event": "voice_turn_trace", "kind": "if_trigger_fired"}]
    assert _run("if_trigger_fired", events=fired, params={"min_triggers": 1}).status == "pass"
    assert _run("if_trigger_fired", events=[], params={"min_triggers": 1}).status == "fail"
    assert _run("if_trigger_fired", events=fired).status == "skipped"


def test_endpoint_telemetry_must_be_present_when_adaptive_endpointing_ran() -> None:
    turns = [{"event": "voice_turn_latency", "endpoint_category": "complete"}, {"event": "voice_turn_latency", "endpoint_category": "unfinished"}]
    assert _run("endpoint_telemetry", events=turns).status == "pass"
    assert _run("endpoint_telemetry", events=[{"event": "voice_turn_latency"}]).status == "fail"
    assert _run("endpoint_telemetry", events=[]).status == "skipped"


def test_premature_responses_are_counted_against_a_limit_and_avoided_ones_are_reported() -> None:
    ev = [{"event": "voice_turn_event", "kind": "premature_response_suspected"}, {"event": "voice_turn_event", "kind": "false_ending_avoided"}]
    assert _run("premature_responses", events=ev, params={"max_premature": 1}).status == "pass"
    assert "1 false ending(s) avoided" in _run("premature_responses", events=ev, params={"max_premature": 1}).detail
    assert _run("premature_responses", events=ev).status == "fail"  # default limit 0


def test_noise_drops_need_a_human_to_confirm_none_was_real_speech() -> None:
    drop = [{"event": "voice_turn_event", "kind": "noise_turn_dropped"}]
    assert _run("noise_drops", events=drop).status == "manual"
    assert _run("noise_drops", events=[{"event": "voice_turn_latency"}]).status == "pass"


def test_the_phase5_scenarios_exist_and_are_ready_to_run() -> None:
    catalog = json.loads((ROOT / "scripts" / "voice_validation_scenarios.json").read_text(encoding="utf8"))["scenarios"]
    by_id = {s["id"]: s for s in catalog}
    assert {f"P0{i}" for i in range(1, 8)} <= set(by_id)
    for sid in (f"P0{i}" for i in range(1, 8)):
        s = by_id[sid]
        for check in s["auto_checks"] + s["manual_checks"]:
            assert check in call_verify.CHECKS, (sid, check)
        for flag in s["flags_required"]:
            assert flag.lower() in config_check.BEHAVIOUR_FLAGS, (sid, flag)
    assert by_id["P04"]["params"]["exact_lines"][0] == LINE and len(by_id["P04"]["params"]["exact_lines"]) == 2
    assert all(by_id[f"P0{i}"]["direction"] == "outbound" for i in range(1, 8))
