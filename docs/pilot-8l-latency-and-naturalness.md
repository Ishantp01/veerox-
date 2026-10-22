# Phase 8L: voice latency and naturalness (offline work; no call was placed)

Evidence: the Phase 8K call log `.validation-8k/live-api.log` and Plivo's record of that call. Times are UTC on 22 Oct 2026.
"Proven" = read from a timestamp or provider record; "estimate" = derived; "hypothesis" = plausible, not shown.

## 1. The 19-second greeting delay

| UTC | Event | Status |
|---|---|---|
| 05:12:04 | Plivo: call initiated (our log: 05:12:05.9; the clocks differ by 1-2 s) | proven |
| 05:12:25 | Plivo: callee answered | proven (provider record) |
| 05:12:29.6 | our answer handler finished (`plivo_answer_served`) | proven; 4.6 s after the answer, which includes transit and clock skew |
| 05:12:30.4 | media WebSocket open | proven |
| 05:12:35.3 | `voice_stream_connected` | proven: 4.8 s after the socket opened |
| 05:12:39.4 | greeting requested from the model | proven: 4.2 s later |
| 05:12:40.0 / 40.4 | first model text / ElevenLabs socket ready | proven (612 ms and 940 ms into the turn) |
| 05:12:44.0 | first greeting audio sent | proven; 4,574 ms after the request, of which **3,633 ms was ElevenLabs time to the first audio chunk** |

Answer to first audio: about 19 s.

Causes, with confidence:
1. **The pre-connected OpenAI session had expired (proven from the code and the dial/answer times).** It is started at dial time and lived a flat 20 s; the call rang for about 24 s. That `answer()` then started a fresh one and the stream waited for it is the inferred consequence, consistent with the 4.8 s before `voice_stream_connected` (the log does not say whether a session was claimed; it does now: `precall_claimed`).
2. **A serial start-up (proven from the code, magnitude estimated).** After the handshake the bridge opened the conversation (about five database round trips, estimated 4.0 s at the measured ~0.8 s each: `voice_recording_not_started` was logged 0.79 s after the markup, i.e. one Org read), then read the org context, then the greeting and voice, one after another. That is the 4.2 s before the greeting request.
3. **The answer handler waited for two database writes before returning its markup (proven from the code, estimated 2-3 s on this link).**
4. **The greeting was synthesised live on a cold voice (proven: 3.6 s first-chunk time for the greeting against 0.30 s for the reply later in the same call).** A cold start is a hypothesis; the cached greeting removes it either way.
5. Not found to be a cause: the OpenAI response (612 ms), the ElevenLabs connect (328 ms), audio queue or playback blocking.

Missing instrumentation (now added): when the answer request arrived, and the stages between answer and first audio. `voice_startup_timeline` logs them once per call.
The measurements were taken from a laptop to remote Neon, Upstash and OpenAI; the same code on the deployed region will have much cheaper round trips. Measure it there before drawing conclusions about the final product.

## 2. The 7.8 s and 11.2 s replies

From the per-stage line of turn 2: speech stopped to commit 1 ms, **commit to reply request 7,140 ms**, model 651 ms, TTS connect 343 ms, first chunk 296 ms.
- The reply never depends on the transcript (shown by a test), so the 7.1 s was waiting.
- Reply 1 (11.2 s from the callee's "hello") mostly waited for the greeting, which had not even started when the callee spoke.
- **Bug found and fixed:** a nod ("haan") while the agent was talking was logged as ignored but answered. Every ordinary turn's transcript had been appended to `overlap_transcripts` (a fresh commit always has `reply_pending` set), nothing emptied the list in the default mode, and the backchannel drop only runs when the list is empty. Reproduced offline, fixed, regression-tested. The agent therefore waited for its own earlier audio and then answered a nod.
- **Transcripts arrive about 4.7-4.8 s after speech stops (proven, both turns).** Why is unknown (OpenAI-side processing or the link). It matters because the nod filter depends on it. The live partial-transcript path was not ready until 05:12:45; it now starts earlier, but whether it produced anything for "haan" is not logged. `voice_turn_trace` now records transcript arrival, nod decisions and what each reply waited for.

## 3. Changes (all offline; flags unchanged)

| File | Change |
|---|---|
| `channels/voice/realtime_bridge.py` | Pre-connected session lives ring timeout + 35 s (60 s here). Greeting-cache warm-up starts first, beside the OpenAI connect. Conversation open, org context, greeting and voice reads start together. A cached greeting is sent before the conversation row or model session are needed (flag `VOICE_GREETING_AUDIO_CACHE`, off by default; a miss, consent or notice org falls back to the live greeting). A start-up that ends early cancels or closes the row it started. |
| `channels/voice/adapter.py` | `send_cached_audio_early` / `adopt_early_greeting` (the model is told what was said, the greeting is guarded from the callee's first "hello", persisted once, never spoken twice, partial sends adopted). Overlap-list fix. `voice_turn_trace` lines. One media envelope builder. |
| `channels/voice/webhook.py` | The answer returns its markup first; the usage write and the recording decision run after the response. |
| `channels/voice/latency.py` | Start-up timeline marks and the single `voice_startup_timeline` line (stage names and milliseconds only). |
| `channels/voice/signature.py` | A rejected request now logs the provider's status words (call status, event, direction, hangup cause) so the next call shows what the post-hangup request was. No new endpoint: Plivo's behaviour for outbound calls without a `hangup_url` is not documented, so it is not assumed. |
| `docs/pilot-org-config.preview.json` and the pilot org's script | Outbound framing, short Hinglish turns, nods are not questions. Applied to the **pilot** org only (previous text saved in `.validation-8l/script-before.txt`). |
| `preview_phrases.json`, `docs/pilot-8l-voice-candidates.json` | Data for the listening test (section 5). Nothing synthesised. |
| tests | `test_voice_startup_8l.py` (26), `test_voice_turns_8l.py` (10); one inventory entry in `test_voice_multi_org.py`. |

## 4. Latency: baseline and offline-validated

| | Baseline (live call) | After (offline, slow-link model) | Live expectation |
|---|---|---|---|
| Greeting request after the stream opens | about 9 s (4.8 s claim wait + 4.2 s serial reads) | 4.0 s live greeting, 0.8 s cached audio, with the model of the live link (0.8 s per round trip, 5 round trips to open the conversation) | to be measured |
| First audible greeting after answer | about 19 s | not simulated end to end | the cache removes the 3.6 s cold TTS and the model wait; target about 3-5 s on this link, below 3 s on the deployed region: unproven |
| Our scheduling overhead per turn | not measured | p50 0.0 ms, p95 0.0 ms, worst 0.0 ms over 40 simulated turns (rounding of an in-process check) | the waits are OpenAI, transcription, ElevenLabs and the link |
| Reply after the caller's clear turn | 7.8 s (turn 2), 1.3 s after the request was sent | the reply no longer waits for the transcript and a nod is no longer answered | 1-2 s only if transcription is not the bottleneck |

These are simulated delays. They prove ordering and overlap, not real-world speed.

## 5. "Robotic" voice: what is known and the listening test

Known (read from the code and a read-only ElevenLabs metadata lookup):
- The voice is a professional Hindi voice ("Armaan - Local Outbound Sales Agent"), verified for Hindi on the flash model, so a wrong-language voice is not the cause.
- Model `eleven_flash_v2_5`, platform default settings (stability 0.5, similarity 0.8, no speed), Roman-script text, audio requested as 8 kHz mu-law straight from ElevenLabs, forwarded without resampling. Replies under about 125 characters are generated as one piece, so chunk boundaries are not suspected.
- Not heard by anyone yet: the greeting was not heard on the live call.

Candidate causes, in the order I would test them: Roman-script Hindi through the flash model; the flash model itself; stability; long sentences (addressed in the script); the telephone band (cannot be changed).
`docs/pilot-8l-voice-candidates.json` holds seven candidates (baseline, turbo, lower stability, slower speed, speech normalisation, another Hindi sales voice, multilingual v2) and `preview_phrases.json` now has eight pilot phrases in Roman and Devanagari. Running `python scripts/voice_compare.py --candidates-file docs/pilot-8l-voice-candidates.json --categories pilot_outbound` makes about 5,200 characters of paid ElevenLabs synthesis; it needs your approval. A person should rate the output; none is rated here.

## 6. Next live test (not executed)

Change from the Phase 8K call: `VOICE_GREETING_AUDIO_CACHE=true` in `.env.pilot` (the one flag), the 8L code, and the updated pilot script. Everything else, the handset and the beats of the runbook stay the same.
Before dialling: stream-ownership record still present (`pilot_security_status.py`), preflight 0 failed, exactly one attempt authorised again.
Compare with the first call, from `voice_startup_timeline`, `voice_turn_latency`, `voice_turn_trace` and the callee's report:

| Measure | Phase 8K call | Pass if |
|---|---|---|
| Answer to first greeting audio | about 19 s | at most about 5 s here (record the slow-link caveat) |
| `voice_stream_connected` after the socket opens | 4.8 s | under 1 s, `precall_claimed=true` |
| Reply requested after the caller stops, agent idle | 7.1 s (agent speaking) | under about 2 s, and no waiting that is not the agent's own audio |
| A "haan" while the agent talks | answered | not answered (`voice_turn_trace` kind `backchannel`, `dropped: true`) |
| First reply wording | "what do you want to talk about?" | states why it called, one easy question |
| Voice | "robotic" | rated by you; note it before and after any A/B change |
| Post-hangup request | 403, cause unknown | `voice_webhook_rejected` shows `plivo_call_status` / `plivo_event` |

Stop conditions and rollback are those of `pilot-8h-call-runbook.md`. To undo the 8L script, PATCH the previous text from `.validation-8l/script-before.txt` back; to undo the flag, remove the line.
