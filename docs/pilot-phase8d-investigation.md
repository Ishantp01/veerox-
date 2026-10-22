# Phase 8D: outbound pilot defect investigation

Date: 21 October 2026. Local changes only; no calls, paid API requests, production database writes, provider configuration changes, deployment, or commit.

## Evidence and limits

Continued from the supplied Claude conversation, which ended during Step 6. Independently inspected the current code and parsed the original local pilot output using an allowlist of operational fields. Customer transcripts, numbers, credentials, and call identifiers are omitted here. The organization audit below is attributed to Claude's supplied read-only query; it was not rerun against the database.

The source contains 639 structured records, 20 cuts (12 `live_fallback`, eight `live_real`), and one `conversation_already_has_active_response` error. Contrary to the initial summary, logged reconnects occur at 72.9 seconds in call A and 38.2 and 114.7 seconds in call B. Only two have an accompanying `openai_ws_closed` record; the third is consistent with an iterator ending normally without raising.

## Findings and fixes

### Socket lifecycle

Confirmed code defect: the event handler can send text to ElevenLabs, whose WebSocket library raises the same `ConnectionClosed` class as OpenAI. The bridge previously treated that exception as proof that OpenAI closed, exited its pump, and reconnected. A provider-specific TTS exception and an offline regression now distinguish this case. An interrupted TTS send no longer restarts the Realtime leg. TTS opening after a concurrent cut is closed without forwarding stale speech.

Confirmed instrumentation gap: normal iteration ending did not log a closure. The extracted pump now records normal and exceptional iterator closure, close code, caller-cleanup versus peer closure, and close-frame ordering when the library exposes it. Raw provider close reasons are omitted. Handler errors record exception classes without provider payloads. A generic handler socket exception on an apparently open OpenAI connection remains explicitly unattributed.

Confirmed additional defect: the caller receive pump could terminate when audio forwarding encountered a closed OpenAI socket. It now tolerates that failure and pauses forwarding during reconnect setup/context restoration. Outage audio is dropped rather than replayed; callers may need to repeat words spoken during a genuine outage. Abandoned sockets are closed before replacement.

No configured Realtime session-duration cap was found. Heartbeats remain at 20-second ping interval and timeout. Caller stop/disconnect and final cleanup intentionally close OpenAI. The new diagnostics identify these paths without interpreting code 1000 as a timeout. Unexpected iterator closure still triggers the existing bounded reconnect behavior.

Hypothesis, not a proven historical cause: all three reconnects followed active-generation cuts, so an ElevenLabs interruption race plausibly caused unnecessary OpenAI reconnects. The old logs cannot identify the originating socket, and correlation cannot establish who sent the historical close frame. No basis exists to claim an OpenAI 70–90 second limit.

### Interruption behavior

Confirmed: 20 cuts across the two calls; 12 used the fallback timer. Eight classifier failures logged empty error strings. Timeout is plausible but unproven; future classifier failures log exception type. Cut records now include caller-speaking state, partial-transcript length, queued playback time, and filler verdict without logging speech.

Claude's supplied transcript audit found no shared-word echo indicator or configured noise/backchannel matches in the 25 caller messages. This does not rule out acoustic echo, transcription errors, or short acknowledgments misclassified as real interruptions. The logs cannot conclusively classify every cut. No VAD thresholds were changed. Existing cancellation breaker, watchdog, filler/noise classification, generation checks, and flag-off behavior remain covered by targeted tests.

### Active-response conflict

Confirmed code defect: tool-argument handlers issued `response.create` before their originating response's `response.done`. Parallel tools could issue multiple continuations. Tool outputs now queue one continuation, sent after completion; call-control goodbye continuations follow the same rule. Concurrent pending-reply tasks check the response reservation before sending. Reconnect resume reserves the slot before normal reply scheduling can compete.

The pilot error follows a tool-dispatch episode and caller transcription activity. The prior telemetry does not identify the exact conflicting requests, so this fixes reproduced sources of conflict without claiming the historical event is fully traced. The provider error is logged by code and does not trigger an automatic duplicate response retry.

## Latency evidence

All values below are measured milliseconds, rounded to whole numbers by existing telemetry.

| Outlier | Commit to request | Request to model output | TTS connect wait | TTS first chunk after ready | Forwarding | Total |
|---|---:|---:|---:|---:|---:|---:|
| Call A greeting | unavailable | 541 | 356 | 4585 | 1 | 5483 |
| Call A reply | 2764 | 1086 | 328 | 277 | 0 | 4455 |
| Call A worst reply | 8793 | 481 | 291 | 1047 | 0 | 10614 |
| Call B worst reply | 0 | 1021 | 345 | 3773 | 0 | 5141 |
| Call B later reply | 0 | 687 | 318 | 3391 | 0 | 4396 |

The worst call A reply is dominated by waiting before requesting the response, consistent with its `agent_was_busy` marker and the mute-and-wait turn policy. The worst call B replies and call A greeting are dominated by TTS first-audio delay. Forwarding is 0–1ms at measured resolution. Rounded segments can differ from totals by 1ms.

Missing measurements: physical end of speech to VAD notification, separate decision processing within the request gate, normalizer buffering, provider playback start, and network/acoustic echo. The observed VAD-notification-to-commit segment was 0ms in these reply outliers; that is not proof of zero endpoint-detection latency. Normalization was reported disabled. No evidence establishes why ElevenLabs took several seconds on individual turns. These measurements do not justify blanket model or VAD changes.

## Privacy

Pilot access logging is disabled during logging setup and both pilot launch runbooks now specify `--no-access-log`. Uvicorn WebSocket/error logs propagate through the privacy formatter. A final redaction pass covers formatted exception traces, foreign logging messages, URL-encoded plus signs in caller query values, and close reasons. Repeated redaction preserves already masked values. Operational event names, exception classes, durations, and error codes remain available.

Regression tests exercise real logging handlers with synthetic caller numbers in WebSocket URLs and exception traces, including `%2B` encoding. Pattern-based redaction remains subject to the existing privacy module's documented limitations; old log files are not rewritten by this change.

## Phase 7 audit and incremental plan

Claude's supplied read-only query reported `conversation_mode=strict_script`, `conversation_voice_mode=shadow`, no organization voice profile, no custom greeting/brand, and zero scripts, knowledge items, and scenario-template rows. This means the live voice decision layer was not steering replies, and an org profile was not enabling natural Hinglish or normalization. Built-in scenarios and global prompt defaults still exist; an empty scenario-template table alone does not disable them. The supplied audit did not establish the published `enhanced_dialogue` configuration value. Current code defaults smart turn-taking and reconnect recovery off; the pilot env does not explicitly override them.

Business readiness is therefore unconfirmed: an empty business knowledge/script store must not be described as correctly configured merely because calls connected. Before a future call, verify the intended organization, approved business facts, permitted actions, greeting, and published config version with the owner. This requires separate authorized configuration work; nothing was written here.

Apply changes separately, offline first, with an explicit rollback to each previous value:

1. Review these defects and privacy changes; keep behavior flags unchanged. Validate a controlled call only after approval and business readiness checks.
2. Enable `VOICE_RECONNECT_RECOVERY` alone after confirming text replay privacy and action deduplication. Offline fault injection is the primary reconnect check; do not deliberately disrupt a paid call.
3. Enable bounded TTS recovery alone, then separately evaluate nonblocking TTS finish and prewarming against first-audio/tail-interruption measurements. Do not lower timeouts based on two samples.
4. Move the intended org from shadow to live voice decision mode only with approved business knowledge, action permissions, and a reviewed published config. Observe the basic decision behavior before enabling `settings.enhanced_dialogue` in a subsequent version.
5. Enable `VOICE_SMART_TURN_TAKING` separately. Compare cut reasons, caller-speaking state, filler verdicts, request gating, and response-conflict counts.
6. Enable the org profile's `natural_hinglish` separately, review approved phrases, then enable `normalize_speech` in another step with number/date/currency examples. Preserve pronunciation and business-fact accuracy.

## Files changed by this continuation

- `apps/api/channels/voice/adapter.py`: interruption race guards, tool continuation queue, response reservation, cut instrumentation.
- `apps/api/channels/voice/elevenlabs_client.py`: provider-specific send failure.
- `apps/api/channels/voice/realtime_bridge.py`: socket-specific lifecycle pump, audio forwarding during outage, reconnect setup guards.
- `apps/api/channels/voice/turn_controller.py`: classifier exception-type diagnostics.
- `apps/api/core/privacy.py` and `apps/api/logging.py`: final-output and Uvicorn redaction.
- `apps/api/tests/test_pilot_8d.py`: 12 offline regressions.
- `apps/api/tests/test_voice_tts_turns.py`: replace a 10ms playback sleep with deterministic clock advancement for Windows.
- `docs/local-pilot-runbook.md` and `docs/pilot-remote-runbook.md`: access-log launch flag.
- This report.

Other dirty files predate this continuation and were preserved.

## Validation and readiness

The broader targeted run passed **664 tests**, with six warnings, covering interruptions, reconnect history/org isolation/action deduplication, privacy, tools, TTS recovery and delayed playback, latency, turn classification, Phase 7 integration, pilot compatibility, voice decisions, and simulated calls. After the final delayed-open/redaction adjustments and two additional regressions, **69 focused tests passed**, including all **12 new regressions**. These runs overlap; their counts must not be added as distinct tests. Ruff passed for the new regression file, privacy module, and logging module; compilation checks passed for changed runtime modules.

An earlier run encountered three inaccessible host-temp fixtures and a Windows clock-resolution-sensitive test. Rerunning with a workspace-local test temp directory and deterministic playback time resolved these failures. The full application suite was not run.

Ready for review and offline validation. **Not yet cleared for another paid outbound call**: historical socket ownership, acoustic interruption quality, provider tail latency, actual session configuration acceptance, and business configuration still require controlled validation or owner review. No live performance improvement is claimed. Stop here for approval before any live call, deployment, or configuration activation.
