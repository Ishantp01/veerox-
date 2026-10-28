# Phase 5: controlled voice quality and real-call validation plan

Prepared 28 Oct 2026. **PREPARED, NOT AUTHORIZED. No call, deployment, commit, staging change or production change has been made.**
Nothing here claims an improvement: every "better" below is a hypothesis until measured and listened to.
No secret, phone number, e-mail or customer data appears in this file.

This plan sits on top of `docs/pilot-controlled-call-checklist.md` and `docs/pilot-remote-runbook.md`. Where they are stricter, they win.

## 1. What "staging" is, verified read-only on 28 Oct 2026

Staging is the pilot file `.env.pilot` (`ENVIRONMENT=pilot`), run on this machine behind a tunnel, against a separate pilot database and Redis.

| Item | Finding | Verdict |
|---|---|---|
| Staging ElevenLabs voice | A professional Hindi voice ("Armaan - Local Outbound Sales Agent"); ElevenLabs confirms it exists for this account | OK |
| Dev `.env` voice | **Rejected by ElevenLabs ("voice not found")**. It must not be used for any call | Fix before use |
| TTS path | `VOICE_TTS_PROVIDER=elevenlabs`, engine unset (= ElevenLabs), model `eleven_turbo_v2_5` | As intended |
| Dialogue / STT | `gpt-realtime-2.1` (text output); `whisper-1` in-session; `gpt-live-transcribe` second connection (`VOICE_LIVE_TURN_DETECTION=true`) | As intended |
| VAD | `VOICE_VAD_SILENCE_MS` unset, so the code default **1100 ms** (the dev `.env` sets a different value; it must not be loaded) | OK, baseline |
| Allowlist | `PILOT_ALLOWED_NUMBERS`: 1 entry; `PILOT_ALLOWED_EMAILS`: none | OK |
| Call limits | `PILOT_MAX_OUTBOUND_CALLS=1`, provider time limit 120 s, ring timeout 25 s, workers off, no startup webhook registration | OK (the cap of 1 limits a run to one call; see section 9) |
| Webhook signatures | enforced (pilot default) | OK |
| Logging | privacy masking on, **transcripts not logged** | OK; see section 7 for how fidelity is checked without them |
| Data stores | A Neon database and an Upstash Redis whose hosts differ from the main `.env`; the offline preflight passes the isolation checks | OK, with the warnings below |
| Experimental flags in `.env.pilot` | `VOICE_RECONNECT_RECOVERY=true` (the only one) | Turn off for the baseline |

Offline preflight (`python scripts\pilot_preflight.py .env.pilot`, reads two local files, no network) result: all isolation checks PASS, with these items needing your decision:

1. **WARN, credentials identical to the main `.env`:** the OpenAI, ElevenLabs and Plivo credentials, the encryption key and the admin token are the same as in the main `.env`. Staging therefore spends on the same provider accounts as everything else. Whether those accounts are also the production ones cannot be seen from here.
2. **WARN, same tunnel URL as the main `.env`:** only one of dev and staging can run behind it at a time.
3. **WARN, database name does not contain "pilot" or "test".** The declared database name does match `PILOT_DATABASE_NAME`.
4. **FAIL (procedural), `VEEROX_ENV_FILE` is not set in the shell.** Without it the API also reads the main `.env` for anything the pilot file omits. It must be set to the pilot file before starting the API.
5. The runbook treats this Neon/Upstash pair as the previous production stores unless a new project was created. Confirm with `pilot_preflight.py .env.pilot --connect` (read-only) before any call.

## 2. Blind listening test (needed before any model is chosen)

`D:\adrsproject\veerox-\.validation-voice-ab\ratings.json` **does not exist yet**, so no ranking has been computed and **no final model is chosen**. The 37 clips were generated on 28 Oct 2026.

1. Open `D:\adrsproject\veerox-\.validation-voice-ab\rate.html` in a browser. Do not open `key.json` first.
2. Use headphones at one volume. Rate each clip 1-5 for naturalness, pronunciation and expressiveness; clips under a sentence are comparable with each other.
3. Click **Export ratings**; it downloads `ratings.json`. Move it to `D:\adrsproject\veerox-\.validation-voice-ab\ratings.json`.
4. Score: `python scripts\voice_ab_samples.py --score .validation-voice-ab\ratings.json`. Ask a second listener to repeat it; one rater is not enough to name a winner.

## 3. Configurations (model, voice and script held constant)

The model and voice are not changed in this experiment (turbo v2.5, the Armaan voice); this tests the flags. The model decision follows the listening test.

**B (baseline):** the pilot file with every experimental flag off.
```
VOICE_RECONNECT_RECOVERY=false          # the only experimental flag currently on
VOICE_SCRIPT_AUTHORITY=false   VOICE_EXACT_LINES=false   VOICE_IF_TRIGGERS=false   VOICE_HUMAN_STYLE=false
VOICE_CONVERSATION_MEMORY=false   VOICE_OPENER_VARIETY=false
VOICE_TTS_PHRASE_FLUSH=false   VOICE_ADAPTIVE_ENDPOINTING=false
```
**I (improved, all new flags):**
```
VOICE_SCRIPT_AUTHORITY=true    VOICE_EXACT_LINES=true    VOICE_IF_TRIGGERS=true    VOICE_HUMAN_STYLE=true
VOICE_CONVERSATION_MEMORY=true   VOICE_OPENER_VARIETY=true
VOICE_TTS_PHRASE_FLUSH=true    VOICE_ADAPTIVE_ENDPOINTING=true
# VOICE_ADAPTIVE_VAD_SILENCE_MS=700 and VOICE_ADAPTIVE_SETTLE_MS=250 are the defaults; leave unset
```
Enabling all at once cannot say which flag helped or hurt. If I differs from B, repeat with one group at a time:
**G1 conversation** (authority, exact lines, IF triggers, human style, memory, opener variety), **G2 turn-taking** (adaptive endpointing), **G3 voice** (phrase flush).

## 4. Scenario matrix

All Phase 5 calls are **outbound to the one allowlisted handset**: inbound calls need a Plivo webhook change, which the pilot checklist forbids. Scenario definitions are in `scripts\voice_validation_scenarios.json` (P01-P07, plus the existing S-series). Each is run once under B and once under I with the same script, voice and tester.

| ID | Language | What the tester does | Main checks |
|---|---|---|---|
| P01 | Hindi | Pauses mid-sentence 1.5 s; says "ek minute, soch raha hoon" and waits 3 s | felt cut off, premature responses, endpoint telemetry |
| P02 | English | Interrupts a long answer; says a bare "okay" | interruption stop time, stale audio, bare "okay" ignored |
| P03 | Hinglish | Gives name and budget, corrects the budget, changes topic and comes back | corrected value used, no repeat questions |
| P04 | Hinglish | The EXACT / REQUIRED / IF script of section 6; asks "iska price kya hai?" | exact lines word for word, no lead-in, keyword rule fires, city confirmed |
| P05 | Hinglish | Gives name and number unprompted, then talks for 6+ turns | no re-asking |
| P06 | Hinglish | "haan / achha / theek hai", one cough, one tap, 5 s silence, then a real question | noise handled, real question answered at once |
| P07 | Hinglish | A natural ten-turn call with an objection and a correction | varied openers, rhythm, no robotic breaks |

**Minimum round:** P01, P04, P05 under B and I = **6 calls**. **Full round:** P01-P07 under B and I = **14 calls**. Each call is 60-90 s with 4-8 caller turns; the tester hangs up by 90 s.

Cost is a planning estimate from the existing checklist (about $0.14-$0.29 of AI usage plus about Rs 0.57 telephony per 90 s call, at its planning rate of Rs 100 per USD): about **Rs 180 for 6 calls and Rs 420 for 14 calls as an upper planning bound, not a measured bill**. The checklist records that a **hard per-call monetary gate does not exist**; the controls are the provider 120 s time limit, the call-count cap and a manual stop.

## 5. Measurements (what each run records, and what it cannot)

| You asked for | Where it is recorded | Status |
|---|---|---|
| Speech end to first audible response | `voice_turn_latency.total_ms`: speech stopped to first audio frame **handed to Plivo** | Recorded, but **not "audible"**: network, Plivo's buffer and the handset add time we cannot observe |
| VAD and endpoint hold | `vad_silence_ms`, `endpoint_hold_ms`, `endpoint_category`, event `endpoint_hold` | Recorded (adaptive runs); baseline shows the fixed 1100 ms |
| LLM first token | segment `request_to_model_output` (includes tool rounds) | Recorded |
| LLM first phrase | `tts_phrase_buffer_ms` (how long phrase buffering held the first words) and `tts_chars_at_first_chunk` | **Added in this phase**; zero when phrase flush is off |
| ElevenLabs time to first audio | `tts_connect_ms`, segment `tts_first_chunk`, new `tts_send_to_audio_ms` | Recorded |
| Playback start | `voice_turn_playback.est_playback_ms` is an estimate from bytes sent | **Not measurable from the server.** Calibrate with a second recording device on the handset for 5 sample turns and compare in an audio editor |
| Interruption stop time | `voice_interruption_latency.detected_to_cleared_ms` (clear sent to Plivo) | Recorded; "clear sent", not "heard silence" |
| Premature responses | event `premature_response_suspected`; `false_ending_avoided` shows holds that worked | Heuristic counts |
| Noise-turn drops | event `noise_turn_dropped` | Counts; a person confirms none was real speech |
| Repeated questions | event `repeat_question` (needs memory on) | Counts |
| Script compliance | `exact_lines_verbatim`, `if_trigger_fired` checks; REQUIRED is a manual read | Exact and IF automatic; REQUIRED manual |

Summaries: `python scripts\voice_latency_report.py api.log` prints n, p50, p95 and p99 for every segment and the new notes, plus event counts per 100 turns and endpoint-category counts. **Percentiles are reported only with their n.** Three calls of about 8 turns give about 24 turns per configuration: enough for medians and ranges, **not enough to quote a p95**. Treat p95 as unknown unless a configuration has at least 30 turns.

## 6. Staging test script for P04 (invented details only; applying it writes to the staging database and needs approval)

```
Aap Veerox ke AI assistant hain. Is call ka maksad ek chhota demo schedule karna hai.
Greeting ke baad bolo: [[EXACT: Yeh call quality aur training ke liye record ho sakti hai.]]
Phir caller ka shehar poochho. [[REQUIRED: caller ka shehar confirm karo]]
[[IF said "price"|"cost"|"kimat"|"कीमत": [[EXACT: Pricing paanch hazaar rupaye mahine se shuru hoti hai.]] ]]
Demo ke liye ek din aur samay poochho aur confirm karo.
```
The previous staging script text must be saved first so it can be restored (section 10).

## 7. Procedure for each call

1. Pre-flight (offline): `$env:VEEROX_ENV_FILE=".env.pilot"`; `python scripts\pilot_preflight.py .env.pilot`; check `GET /diag/voice-config` shows exactly the flags of the configuration being tested.
2. Write down the configuration name and run number; one call only; start the API fresh so the one-call counter is at 0.
3. Tester follows the scenario. Optionally record the call on a second device you own (calibration).
4. After the call: save the API log; export the test conversation's messages (test org only) to a JSON file. Transcript logging stays **off**; fidelity is checked from the exported messages, not from logs.
5. Verify: `python scripts\voice_postcall_verify.py P04 --logs api.log --call-uuid <uuid> --conversation conv.json`. Every check prints pass / fail / manual / skipped; manual items are answered by listening.
6. Record the verdicts and the tester's felt-cut-off / felt-slow notes in one sheet per call before the next call, **without** looking at the other configuration's results.

## 8. Decision rules, fixed before any call

I is considered better than B only if **all** hold across the paired runs, on the measured numbers and not on impressions:
- no exact-line failure and no lead-in before an exact line;
- zero repeated questions under I for the P03/P05 detail the tester gave;
- suspected premature responses under I are not higher than under B, and the tester's "felt cut off" notes are not worse;
- the median of `total_ms` for complete turns is lower under I (report the n, the medians and the range; no p95 below 30 turns);
- the blind listening scores of the call recordings (or the clips of section 2) are not lower under I.

If any fails, the result is "not shown to be better", and the ablation groups of section 3 are run instead of enabling anything.

## 9. Risks of enabling several flags together

| Combination | Risk | Mitigation |
|---|---|---|
| Adaptive endpointing without live text | No speed-up: the original wait is kept | Keep `VOICE_LIVE_TURN_DETECTION=true`; `/diag/voice-config` warns |
| Adaptive endpointing + smart turn-taking / endpoint hold | Adaptive makes the hold decisions; the other flags keep only their interruption roles | Run adaptive without the other two |
| Adaptive floor 700 ms | A pausing caller who is misclassified as finished is cut off earlier than at 1100 ms | `felt_cut_off`, `premature_response_suspected`; the unfinished wait is lengthened to at least today's patience |
| Memory + decision-layer replies | The per-reply instructions carry the base text plus a block | Prompt about 4,200 tokens against about 3,460 today; unmeasured effect on latency |
| Phrase flush + normalizer | Phrase cuts and spoken-form rewriting both shape the text | Compare rhythm in P07; ratings decide |
| Exact lines on native audio | Prompt-only, not guaranteed | Staging uses the ElevenLabs path, so exact lines are substituted |
| Human style without script authority | Fixed sentence and question limits can fight a client script | Turn them on together only |
| Reconnect recovery on in staging today | Can add billable sessions and hide transport faults | Off in B and I for the experiment |
| Everything at once | Cannot attribute a change | Ablation groups G1-G3 |

## 10. Rollback

Every new behaviour is flag-gated and off by default, so rolling back means turning flags off, not changing code.
1. **Flags:** copy `.env.pilot` to `.env.pilot.before-phase5` before editing (it is git-ignored). To roll back, restore that file and restart the API; confirm with `/diag/voice-config`.
2. **Script:** save the staging org's current script text before applying section 6; roll back by writing it back (`scripts\pilot_apply_org_config.py` keeps a preview; the 8L notes record how).
3. **Stop a bad call at once:** hang up on the handset; the 120 s provider limit and `PILOT_MAX_OUTBOUND_CALLS` bound a runaway; stopping the API process ends the stream (set `VOICE_HANGUP_ON_STREAM_END=true` in staging so the provider leg is also closed).
4. **Code:** these changes are uncommitted in the working tree. Because they are flag-gated, no code rollback is needed to return to baseline behaviour; to remove the code, `git restore` the modified files and delete the new ones (list in the Phase 5 report) after a copy is kept.
5. **Data:** delete the test conversations and the test org's rows in the staging database, or delete the staging database project as the runbook describes. Nothing touches production.

## 11. Approvals needed before the first paid call

1. The staging file edits of section 12 and a restart.
2. Writing the section 6 script and a verified restore copy into the staging database.
3. A decision on the cap: `PILOT_MAX_OUTBOUND_CALLS=2` with an API restart between pairs (recommended), or a higher value. The cap is counted per process and a restart resets it (`apps/api/pilot.py`), so it guards against a mistake in one run; it is not an account-level budget.
4. The spending ceiling: the existing checklist proposes Rs 100 for one call; this plan needs roughly Rs 180 (6 calls) to Rs 420 (14 calls) as a planning bound, and the hard monetary gate it lists does not exist. Please name the figure you accept and accept the manual controls.
5. Confirmation of the credential warning (staging uses the main accounts) and of what production's ElevenLabs voice ID is.
6. `ratings.json` from the listening test, or an explicit decision to run calls first.

## 12. Exact staging configuration proposed for approval (changes to `.env.pilot` only)

Round 1, configuration B then I (one flag block at a time, restarting between configurations):
```
# unchanged: ENVIRONMENT=pilot, PLIVO_SIGNATURE_MODE enforced, LOG_TRANSCRIPTS=false, LOG_PRIVACY_ENABLED=true,
#            VOICE_TTS_PROVIDER=elevenlabs, ELEVENLABS_MODEL_ID=eleven_turbo_v2_5, the Armaan voice id,
#            VOICE_LIVE_TURN_DETECTION=true, VOICE_VAD_SILENCE_MS unset (1100), the allowlist, PILOT_WORKERS_ENABLED=false,
#            PLIVO_STARTUP_REGISTRATION=false, VOICE_OUTBOUND_TIME_LIMIT_SECONDS=120, VOICE_OUTBOUND_RING_TIMEOUT_SECONDS=25
VOICE_RECONNECT_RECOVERY=false
VOICE_HANGUP_ON_STREAM_END=true
VOICE_MAX_CALL_SECONDS=100
PILOT_MAX_OUTBOUND_CALLS=2
VOICE_LATENCY_TRACKING=true          # already the default
# then the flag block of section 3 for B, then for I
```
Shell: `$env:VEEROX_ENV_FILE=".env.pilot"`.

## 13. Known gaps

- No measure of when audio is actually heard; the calibration recording in section 5 estimates it for a few turns only.
- REQUIRED points are checked by a person reading the transcript. A non-blocking completion tracker is recommended but not built (see the Phase 4 report); it needs a separate review.
- Fidelity checks use exported conversation messages; if exporting is not possible the exact-line check is "skipped", never guessed.
- The classifier of adaptive endpointing is rule-based and its noise drop depends on the live transcriber being healthy.
- One tester on one handset, one network: results describe this setup only.
