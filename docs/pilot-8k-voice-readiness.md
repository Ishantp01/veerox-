# Phase 8K: final voice-pilot readiness (22 October 2026)

**Verdict: BLOCKED for the live call; the code is ready offline.** Nothing was initialised, rotated, dialled, tunnelled or paid for. Scope: voice calling only.

## 1. What changed in 8K (the minimum)

The blocker was the uninitialised nonce ledger, whose safe initialisation needs proof that the shared Plivo token was rotated. Plivo's nonce has no timestamp, so
the ledger can only be made safe by an epoch, and rotating the shared token would touch production. Instead the answer to a call **we dial** is now authorised by
a single-use ticket whose freshness we control:

- `failover.initiate_call` mints a random ticket (`ct`) before the allowance is spent, stores a hash-bound record in the pilot Redis for 180 s (bound to org, campaign target and destination) and puts `ct` in the answer URL. Plivo signs the whole URL, so the ticket is covered by its signature. If the store is down, enforcement stops the call before anything is dialled.
- `/voice/answer` for an outbound call verifies the Plivo signature first, then takes the ticket with an atomic `GETDEL`. A repeat, a capture, an old request (no ticket), another destination or another org gets 403 with an empty body, before any AI session starts.
- The nonce ledger stays uninitialised on purpose. Inbound answers, hangup and recording callbacks still depend on it, so they stay closed (the pilot dials out only, with no hangup URL and recording off).
- `pilot_security_initialize.py --streams-only` creates only the stream-ownership state; it needs two attestations that are true (old stream tokens expired, workers stopped), not the rotation one. Full form unchanged. `pilot_security_status.py` reports `outbound_call_ready`.
- Earlier in 8J: the initializer now refuses any shell override of the pilot file's Redis or secrets.

Unchanged and reused: Plivo V3 verification (pinned to the SDK's output), the signed stream token with expiry, exclusive stream ownership with release after cleanup, enforcement on, production `auto` = off (no Redis access, URLs unchanged).

### Residual limitations (stated, not hidden)
- If Plivo retried an answer request after the first was consumed, the retry is refused and the call fails (fail closed). Plivo's page does not say whether it retries answer URLs.
- The ticket is a bearer value inside the signed URL until used or 180 s pass; someone who could read the live request in flight could race Plivo (needs access inside TLS, e.g. the local tunnel inspector). After use it is worthless.
- A stream token can be reused sequentially after a clean drop until it expires (300 s); a crash leaves that one call's stream owner locked (affects only that call; there is no operator tool to clear it yet).
- Redis is a dependency: an outage prevents the call (before dialling, without spending the allowance) or refuses the answer.
- The signature algorithm is proven against Plivo's SDK, not against Plivo's live traffic. The shared Plivo token and caller ID are unchanged (production number, production balance).
- Tickets and ownership are in Redis (shared across instances), but the one-call counter and the pre-connected AI session are per process: the pilot must be one process.

## 2. Prerequisites

| Item | Status |
|---|---|
| Neon / Upstash credentials rotated | **BLOCKED**: both still match the values exposed in chat |
| Pilot database and Redis isolated (differ from main `.env`, declared pilot DB, connectivity read-only) | PASS |
| OpenAI spending controls | **BLOCKED**: needs your console confirmation; a model key cannot show them |
| ElevenLabs overage disabled | PASS (API snapshot: extension 0, flags false; 33,024 of 142,420 units used); confirm workspace/key yourself |
| Plivo auto-recharge disabled | PASS (snapshot: prepaid, auto-recharge false, USD 17.20) |
| Allowlist holds only the intended handset | PASS for one configured entry; you must confirm it is the phone you hold |
| Recording off, AI disclosure on | PASS (org policy and greeting; settings) |
| One attempt, 120 s, 25 s ring, one concurrent | PASS (settings and the provider request, mocked) |
| Signature enforcement | PASS (`enforce` via `auto`) |
| Stream-ownership state in the pilot Redis | **BLOCKED**: needs your approval for the one `--streams-only` write |

No hard monetary cap exists. All three accounts are shared with production.

## 3. Offline voice validation (targeted, `.venv`, no full suite)

1,620 tests passed, 0 failed, in four shards: pilot 290, voice core 382, audio/turns/webhooks 486, conversation/dialogue 462.

| Area | Where it is covered |
|---|---|
| 1 Realtime conversation handling | `test_voice_phase6`, `test_voice_simulated_calls`, `test_voice_call_control` |
| 2 ElevenLabs audio | `test_elevenlabs_client`, `test_voice_tts_turns`, `test_voice_tts_recovery` |
| 3 Hindi/Hinglish normalisation | `test_tts_normalize`, `test_voice_profile` (not enabled in the baseline) |
| 4 Natural pauses | `test_turn_controller` (endpoint holds), `test_voice_phase7a/7b` |
| 5 Interruptions | `test_voice_interruption`, `test_turn_controller`, `test_voice_live_turn_stats` |
| 6 Context and corrections | `test_dialogue*`, `test_conversation_continuity`, `test_phase7_integration` (enhanced dialogue is off in the baseline) |
| 7 Reconnect recovery | `test_voice_reconnect`, `test_pilot_8f` |
| 8 Duplicate responses / actions | `test_voice_phase6_tools`, `test_voice_reconnect`, `test_pilot_8h_call_safety` |
| 9 Tool/audio synchronisation | `test_voice_phase6_tools`, `test_voice_tts_turns` |
| 10 Termination and cleanup | `test_voice_call_control`, `test_pilot_8j_replay` (socket lifecycle), `test_voice_tts_recovery` |

Offline means fakes and mocks. Latency, pronunciation, real interruption timing and Plivo's 120 s cutoff are only observable on the live call.

## 4. Single-call checklist (baseline configuration; do not change any flag)

Run only after every BLOCKED item is cleared and you have authorised exactly one attempt. Procedure, stop conditions and rollback: [pilot-8h-call-runbook.md](pilot-8h-call-runbook.md).

| Beat (about 110 s) | Look/listen for | Evidence |
|---|---|---|
| Greeting | One short Hinglish greeting; says it is an AI assistant; names Veerox; no long silence before it | ear; first-audio latency in the log summary |
| Pronunciation | Natural Hindi/Hinglish; numbers and brand name clear | ear |
| Latency | Replies start within about 2 s typically; note the slow ones | `voice_latency` lines, `scripts/voice_latency_report.py` |
| Thinking pause | Does not talk over "ek minute, soch raha hoon..." (3 s silence) | ear; no `voice_agent_cut` |
| Interruption | Stops promptly on "Ruko"; no old words after the cut; no echo of its own voice | `voice_agent_cut`, `voice_live_turn_summary.cuts` |
| Correction | "das hazaar... nahi, paanch hazaar" is acknowledged and later recalled as paanch | ear |
| Context memory | Answers "maine budget kitna bola tha?" correctly; no re-greeting, no repeated question | ear |
| Callback | Asks for the time, repeats it back, waits for "haan", only then says it is noted | callback row |
| Escalation | Says the team will follow up; never claims a person is joining now | ear; handoff row |
| Termination | Short goodbye; line closes after you hang up; ends by about 120 s | log: no stuck call; process shut down cleanly |

Also check afterwards: no unmasked number in the log, `voice_live_turn_summary` present, exactly one new conversation, the earlier records untouched.

## 5. After a clean baseline: one change per call, each needing its own approval

1. **Natural Hinglish** (org voice profile `natural_hinglish`): wording only, lowest risk. Then **speech normalisation** (`normalize_speech`) as its own step.
2. **Smart turn-taking** (`VOICE_SMART_TURN_TAKING`): timing change; compare cuts, holds and false interruptions with the baseline.
3. **Enhanced dialogue**: highest risk (org `live` decision mode, published config, tool holds); last.

Between calls: compare against the baseline numbers, stop on any regression, never enable two at once.

## 6. Rollback

Stop the API and tunnel; remove the added `.env.pilot` settings listed in the runbook; the nonce ledger was never created, and the stream-ownership record can be left (it is inert). To undo business content: greeting `PUT null`, delete the script and the seven knowledge items, or drop the Neon project. Nothing at any provider was changed. Code changes are local and uncommitted.
