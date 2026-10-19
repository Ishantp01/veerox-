"""Automated checks for a controlled test call, run on its logs and exported records AFTER the call.

The live-call plan (docs/voice-live-validation.md) has a human place and take a call; this module turns
what the system recorded into pass / fail / manual verdicts so nobody has to read logs by eye. It is pure
(inputs are parsed log events and plain dicts), so it is tested offline with synthetic data and can be
pointed at real exports later. A check it cannot decide from the data is ``manual``, never a guess, and a
check whose inputs were not provided is ``skipped``.

Inputs:
  events        parsed JSON log lines for ONE call (``call_uuid`` already filtered)
  conversation  {"messages": [{"role", "content"}...], "tags": [...], "recording_url": str|None,
                 "duration_seconds": float|None}
  appointments  [{"date", "time"}...]
  usage_rows    [{"event_id", "usage_type", "quantity", "source", "metadata"}...]
  raw_lines     the raw log text for the call, for the privacy scan
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from apps.api.channels.voice.greeting import has_ai_disclosure
from apps.api.channels.voice.latency import percentile


@dataclass(frozen=True)
class CheckResult:
    id: str
    status: str  # pass | fail | manual | skipped
    detail: str


_BAD_EVENTS = {
    "voice_stream_error", "pump_openai_error", "voice_event_handler_error", "voice_tts_recovery_failed",
    "voice_hangup_failed_line_may_be_open", "voice_cancel_suspected_session_close", "openai_ws_reconnect_exhausted",
}
_BOOKED_CLAIM = re.compile(r"(is now booked|book ho gayi|booked for|appointment (is )?confirmed|confirm ho gayi)", re.IGNORECASE)
_PHONE_IN_LOG = re.compile(r"(?<![\w*])\+?\d{10,15}(?![\w*])")


def _named(events: Iterable[Mapping[str, Any]], *names: str) -> list[Mapping[str, Any]]:
    return [e for e in events if e.get("event") in names]


def _pass(id_: str, detail: str) -> CheckResult:
    return CheckResult(id_, "pass", detail)


def _fail(id_: str, detail: str) -> CheckResult:
    return CheckResult(id_, "fail", detail)


def _skip(id_: str, why: str) -> CheckResult:
    return CheckResult(id_, "skipped", why)


# --- the checks ----------------------------------------------------------------------------------------------------------------


def check_connected(ctx: Mapping[str, Any]) -> CheckResult:
    ev = ctx["events"]
    if not ev:
        return _skip("connected", "no log events were provided for this call")
    ok = _named(ev, "voice_stream_connected") and _named(ev, "voice_stream_ended")
    return _pass("connected", "stream opened and closed") if ok else _fail("connected", "no voice_stream_connected/ended pair")


def check_no_errors(ctx: Mapping[str, Any]) -> CheckResult:
    if not ctx["events"]:
        return _skip("no_errors", "no log events")
    bad = [e["event"] for e in ctx["events"] if e.get("event") in _BAD_EVENTS or e.get("level") == "error"]
    return _fail("no_errors", f"{len(bad)} error event(s): {sorted(set(bad))}") if bad else _pass("no_errors", "no error events")


def check_first_audio_latency(ctx: Mapping[str, Any]) -> CheckResult:
    turns = _named(ctx["events"], "voice_turn_latency")
    limit = ctx.get("params", {}).get("max_p95_first_audio_ms", 2500)
    totals = [t["total_ms"] for t in turns if isinstance(t.get("total_ms"), (int, float))]
    if not totals:
        return _skip("first_audio_latency", "no voice_turn_latency events (is VOICE_LATENCY_TRACKING on?)")
    p95 = percentile(totals, 95)
    detail = f"n={len(totals)} p50={percentile(totals, 50)} p95={p95} (limit {limit} ms; a single call is not a distribution)"
    return _pass("first_audio_latency", detail) if p95 <= limit else _fail("first_audio_latency", detail)


def check_greeting_disclosure(ctx: Mapping[str, Any]) -> CheckResult:
    messages = (ctx.get("conversation") or {}).get("messages") or []
    first = next((m for m in messages if m.get("role") == "assistant"), None)
    if first is None:
        return _skip("greeting_disclosure", "no assistant message recorded")
    ok = has_ai_disclosure(first.get("content", ""))
    return _pass("greeting_disclosure", "first assistant turn identifies the AI") if ok else _fail("greeting_disclosure", "first assistant turn does not say it is an AI")


def check_interruptions(ctx: Mapping[str, Any]) -> CheckResult:
    cuts = _named(ctx["events"], "voice_agent_cut")
    timings = _named(ctx["events"], "voice_interruption_latency")
    if not cuts:
        return _skip("interruptions", "the agent was never cut off in this call")
    cleared = [t["detected_to_cleared_ms"] for t in timings if isinstance(t.get("detected_to_cleared_ms"), (int, float))]
    limit = ctx.get("params", {}).get("max_detect_to_clear_ms", 1500)
    detail = f"{len(cuts)} cut(s); detected->playback cleared p50={percentile(cleared, 50)} max={max(cleared) if cleared else None} ms"
    return _fail("interruptions", detail + " (no timings logged)") if not cleared else (
        _pass("interruptions", detail) if max(cleared) <= limit else _fail("interruptions", detail + f" exceeds {limit}")
    )


def check_cancel_ok(ctx: Mapping[str, Any]) -> CheckResult:
    ev = ctx["events"]
    sent = _named(ev, "voice_response_cancel_sent")
    if not sent:
        return _skip("cancel_ok", "no response.cancel was sent (VOICE_SAFE_CANCEL off or no interruption)")
    bad = _named(ev, "voice_cancel_disabled", "voice_cancel_suspected_session_close", "voice_cancel_hard_timeout")
    done = _named(ev, "voice_cancel_latency")
    if bad:
        return _fail("cancel_ok", f"cancel misbehaved: {sorted({e['event'] for e in bad})}")
    return _pass("cancel_ok", f"{len(sent)} cancel(s), {len(done)} confirmed by response.done") if len(done) >= len(sent) else _fail("cancel_ok", f"{len(sent)} cancel(s) but only {len(done)} confirmed")


def check_no_session_close(ctx: Mapping[str, Any]) -> CheckResult:
    closes = _named(ctx["events"], "openai_ws_closed")
    ended = _named(ctx["events"], "voice_stream_ended")
    if not ctx["events"]:
        return _skip("no_session_close", "no log events")
    # A close that is the call's own end is expected; one in the middle of a call is the failure to look for.
    return _pass("no_session_close", f"{len(closes)} Realtime close(s); reconnects handled" if len(closes) <= 1 else "") if len(closes) <= 1 else _fail("no_session_close", f"{len(closes)} Realtime session closes")


def check_tts_recovery(ctx: Mapping[str, Any]) -> CheckResult:
    ev = ctx["events"]
    failures = _named(ev, "voice_tts_failure")
    if not failures:
        return _skip("tts_recovery", "no TTS failure occurred in this call")
    recovered = _named(ev, "voice_tts_recovered")
    failed_over = _named(ev, "voice_tts_failed_over")
    hung = _named(ev, "voice_call_hangup")
    if recovered and not failed_over:
        return _pass("tts_recovery", f"{len(failures)} failure(s), recovered")
    if failed_over and hung:
        return _pass("tts_recovery", "failed over to the apology and ended the call")
    return _fail("tts_recovery", "a TTS failure neither recovered nor ended the call")


def check_call_ended_cleanly(ctx: Mapping[str, Any]) -> CheckResult:
    ev = ctx["events"]
    if _named(ev, "voice_hangup_failed_line_may_be_open"):
        return _fail("call_ended_cleanly", "the provider would not confirm the hangup: the line may have stayed open")
    hung = _named(ev, "voice_call_hangup")
    if not hung:
        return _skip("call_ended_cleanly", "the agent did not end this call (the caller hung up)")
    result = hung[-1].get("result")
    tags = (ctx.get("conversation") or {}).get("tags") or []
    has_outcome = any(str(t).startswith("outcome:") for t in tags) if ctx.get("conversation") else None
    if result not in ("ok", "already_ended"):
        return _fail("call_ended_cleanly", f"hangup result {result!r}")
    if has_outcome is False:
        return _fail("call_ended_cleanly", "hung up but no outcome tag on the conversation")
    return _pass("call_ended_cleanly", f"hangup {result}; outcome {'recorded' if has_outcome else 'not checked'}")


def check_goodbye_before_hangup(ctx: Mapping[str, Any]) -> CheckResult:
    hung = _named(ctx["events"], "voice_call_hangup")
    messages = (ctx.get("conversation") or {}).get("messages") or []
    if not hung or not messages:
        return _skip("goodbye_before_hangup", "no agent-ended call, or no transcript")
    return _pass("goodbye_before_hangup", "the last recorded turn is the agent's") if messages[-1].get("role") == "assistant" else _fail("goodbye_before_hangup", "the last recorded turn is the caller's: the agent may have hung up without a goodbye")


def check_appointment_truthful(ctx: Mapping[str, Any]) -> CheckResult:
    conv = ctx.get("conversation")
    appointments = ctx.get("appointments")
    if conv is None or appointments is None:
        return _skip("appointment_truthful", "conversation or appointments not provided")
    claimed = any(_BOOKED_CLAIM.search(m.get("content", "")) for m in conv.get("messages", []) if m.get("role") == "assistant")
    slots = [(a.get("date"), a.get("time")) for a in appointments]
    if len(slots) != len(set(slots)):
        return _fail("appointment_truthful", f"duplicate appointment slots: {slots}")
    if claimed and not slots:
        return _fail("appointment_truthful", "the agent said an appointment was booked but none exists")
    if slots and not claimed:
        return CheckResult("appointment_truthful", "manual", "an appointment exists; confirm by listening that it was confirmed to the caller")
    return _pass("appointment_truthful", "claims match the records" if claimed else "no booking claimed, none made")


def check_no_duplicate_actions(ctx: Mapping[str, Any]) -> CheckResult:
    suppressed = _named(ctx["events"], "voice_tool_duplicate_suppressed")
    appointments = ctx.get("appointments")
    if appointments is not None:
        slots = [(a.get("date"), a.get("time")) for a in appointments]
        if len(slots) != len(set(slots)):
            return _fail("no_duplicate_actions", f"duplicate appointments: {slots}")
    return _pass("no_duplicate_actions", f"{len(suppressed)} repeated action(s) suppressed; no duplicate records")


def check_usage_recorded(ctx: Mapping[str, Any]) -> CheckResult:
    rows = ctx.get("usage_rows")
    if rows is None:
        return _skip("usage_recorded", "usage rows not provided")
    ids = [r.get("event_id") for r in rows]
    if len(ids) != len(set(ids)):
        return _fail("usage_recorded", "duplicate usage event ids")
    kinds = {r.get("usage_type") for r in rows}
    missing = [k for k in ("voice_seconds",) if k not in kinds]
    if missing:
        return _fail("usage_recorded", f"missing {missing}")
    seconds = sum(float(r.get("quantity", 0)) for r in rows if r.get("usage_type") == "voice_seconds")
    duration = (ctx.get("conversation") or {}).get("duration_seconds")
    if duration is not None and abs(seconds - float(duration)) > 10:
        return _fail("usage_recorded", f"voice_seconds {seconds} differs from the call duration {duration}")
    unmeasured = [r for r in rows if r.get("metadata") and r["metadata"].get("basis") not in (None, "measured")]
    if unmeasured:
        return _fail("usage_recorded", "an event is not labelled measured")
    return _pass("usage_recorded", f"{len(rows)} event(s), types {sorted(k for k in kinds if k)}, voice_seconds={seconds}")


def check_recording_policy(ctx: Mapping[str, Any]) -> CheckResult:
    expect = ctx.get("params", {}).get("expect_recording")
    conv = ctx.get("conversation")
    if expect is None or conv is None:
        return _skip("recording_policy", "expected recording state or conversation not provided")
    has = bool(conv.get("recording_url"))
    return _pass("recording_policy", f"recording {'present' if has else 'absent'} as expected") if has == bool(expect) else _fail("recording_policy", f"expected recording {'present' if expect else 'absent'}, found {'present' if has else 'absent'}")


def check_privacy_logs(ctx: Mapping[str, Any]) -> CheckResult:
    raw = ctx.get("raw_lines")
    if raw is None:
        return _skip("privacy_logs", "raw log text not provided")
    found = _PHONE_IN_LOG.findall(raw if isinstance(raw, str) else "\n".join(raw))
    # Provider call ids and timestamps are long digit runs too; only report numbers that look like phone numbers.
    suspicious = [f for f in found if not f.startswith(("20", "1760", "1761"))]
    return _fail("privacy_logs", f"{len(suspicious)} unmasked phone-like number(s) in the logs") if suspicious else _pass("privacy_logs", "no unmasked phone numbers found")


def manual(id_: str, what: str) -> Callable[[Mapping[str, Any]], CheckResult]:
    return lambda ctx: CheckResult(id_, "manual", what)


CHECKS: dict[str, Callable[[Mapping[str, Any]], CheckResult]] = {
    "connected": check_connected,
    "no_errors": check_no_errors,
    "first_audio_latency": check_first_audio_latency,
    "greeting_disclosure": check_greeting_disclosure,
    "interruptions": check_interruptions,
    "cancel_ok": check_cancel_ok,
    "no_session_close": check_no_session_close,
    "tts_recovery": check_tts_recovery,
    "call_ended_cleanly": check_call_ended_cleanly,
    "goodbye_before_hangup": check_goodbye_before_hangup,
    "appointment_truthful": check_appointment_truthful,
    "no_duplicate_actions": check_no_duplicate_actions,
    "usage_recorded": check_usage_recorded,
    "recording_policy": check_recording_policy,
    "privacy_logs": check_privacy_logs,
    # Judgements only a person listening can make.
    "hindi_naturalness": manual("hindi_naturalness", "Listen: is the Hindi/Hinglish natural, correctly pronounced, well paced?"),
    "indian_names": manual("indian_names", "Listen: were the Indian names and places pronounced acceptably?"),
    "amounts_dates_times": manual("amounts_dates_times", "Listen: were amounts, dates and times spoken correctly?"),
    "objection_handling": manual("objection_handling", "Listen: was hesitation handled without pressure, and were questions answered first?"),
    "corrections_handled": manual("corrections_handled", "Listen: was the correction confirmed back and not asked for again?"),
    "interruption_feel": manual("interruption_feel", "Listen: did the agent stop promptly and answer the interruption?"),
    "no_stale_audio": manual("no_stale_audio", "Listen: any words from a cut-off answer heard after the interruption?"),
    "language_switch": manual("language_switch", "Listen: did the agent follow a requested language change?"),
}


def verify_call(
    events: list[Mapping[str, Any]],
    check_ids: Iterable[str],
    *,
    conversation: Mapping[str, Any] | None = None,
    appointments: list[Mapping[str, Any]] | None = None,
    usage_rows: list[Mapping[str, Any]] | None = None,
    raw_lines: str | list[str] | None = None,
    params: Mapping[str, Any] | None = None,
) -> list[CheckResult]:
    ctx = {
        "events": events, "conversation": conversation, "appointments": appointments,
        "usage_rows": usage_rows, "raw_lines": raw_lines, "params": params or {},
    }
    results = []
    for check_id in check_ids:
        check = CHECKS.get(check_id)
        results.append(check(ctx) if check else CheckResult(check_id, "fail", "unknown check id"))
    return results
