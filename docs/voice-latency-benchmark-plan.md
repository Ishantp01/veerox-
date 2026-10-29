# Voice latency: controlled local benchmark plan (not executed)

Status: plan only. Nothing here has been run against real audio or real providers. Local mocked tests exist
(`test_voice_end_of_turn_latency.py`, `test_voice_barge_in_turns.py`); they measure only our code's holding time.

## 0. What needs approval before anything below runs

Every item in section 3 needs a real call or real provider traffic, so none of it was started.

| Need | Why | Cost / risk |
|---|---|---|
| Outbound Plivo calls to the allow-listed test phone (`VOICE_DIAL_ALLOWLIST`) | only way to get real VAD, jitter and playback timing | call minutes; the local `.env` shares secrets and the tunnel with prod (see memory: staging isolation) |
| OpenAI Realtime + live-transcribe usage | the model's first-token time is a component | tokens/minutes |
| ElevenLabs synthesis, both models | model and chunk-schedule comparisons | paid characters (about 20 turns x 6 configs x ~300 chars = ~36k chars, estimate) |
| A scripted "caller" | ground truth for "end of actual user speech" | a pre-recorded WAV played from the test phone, with its known end offset |

## 1. Control configuration (prod-like, from the prod `.env` read on 2026-10-29)

`ELEVENLABS_MODEL_ID=eleven_multilingual_v2`, `VOICE_VAD_SILENCE_MS=1000`, `VOICE_SMART_TURN_TAKING=true`,
`VOICE_NATIVE_BARGE_IN=false`, `VOICE_ELEVENLABS_PREWARM=false`, `VOICE_TTS_PHRASE_FLUSH=false`,
`VOICE_COMMIT_TIME_CUT=false`. Same org, same script, same voice, same test phone, same time of day, for every run.

## 2. Matrix: one variable per run, always compared against the control

| Run | Single change from control |
|---|---|
| C0, C0' | none (run twice: C0' measures run-to-run noise; a difference smaller than that is not a result) |
| A | `VOICE_COMMIT_TIME_CUT=true` |
| B | `ELEVENLABS_MODEL_ID=eleven_flash_v2_5` |
| C | `VOICE_ELEVENLABS_PREWARM=true` |
| D | `VOICE_TTS_PHRASE_FLUSH=true` |
| E1 / E2 | `VOICE_VAD_SILENCE_MS=900` / `800` (1000 is the control) |

No run enables more than one change. Any combination is a separate, later round and only from winners.

## 3. Scripted turns per run (same audio file, same order, N = 20 of each class)

1. short question while the agent is idle
2. long question while the agent is idle
3. sentence with a 0.9 s pause in the middle ("mera budget hai ... paanch lakh")
4. acknowledgement while the agent talks ("haan ji", "theek hai", "hmm")
5. background noise or side talk of 1.5-3 s while the agent talks
6. real question spoken over a long (>15 s) agent answer
7. two questions back to back (second starts within 1 s of the first answer beginning)

## 4. What to record per turn (all from existing logs; no new instrumentation needed)

| Measure | Source |
|---|---|
| End of actual user speech | known offset in the scripted audio, aligned to the call start in the Plivo recording |
| VAD speech-end | `voice_turn_latency.marks_ms.speech_stopped` plus `vad_silence_ms` (true last word = speech_stopped - vad_silence_ms) |
| Final transcript | `transcript_final` mark, `commit_to_transcript` segment |
| Decision to answer | `voice_turn_trace` `reply_firing`, `commit_to_request` segment |
| Response start / first text | `response_requested`, `openai_first_output` |
| TTS ready / first chunk / first audio sent | `tts_ready`, `tts_first_chunk`, `first_audio_sent`; `tts_chars_at_first_chunk`, `tts_send_to_audio_ms` |
| Audible first audio | Plivo recording, minus end of speech; `first_audio_sent` is only a lower bound |
| Interruption latency | `voice_interruption_latency` (`detected_to_cleared_ms`, `detected_to_cut_decision_ms`) and, in the recording, last agent sample after the caller's first word |
| False interruptions | classes 3, 4, 5: any `voice_agent_cut` or `false_interruption` event that the script did not call for |
| Premature answers | class 3: any `reply_firing` before the sentence's end |

Report median and p95 with n; drop nothing silently; keep the raw log lines for each run.

## 5. Pass criteria (set before running, so the result cannot be argued afterwards)

- A run counts as an improvement only if median end-of-speech to first audio drops by more than the C0 vs C0' difference.
- Zero new false interruptions in classes 3-5 compared with control, and zero premature answers in class 3.
- Interruption latency (class 6) is reported, not traded away for speed.
- Flash vs multilingual (B) and phrase flush (D) also need a human listening check; latency alone does not accept them.

## 6. Recommended rollout order (each step only if the previous one passed)

1. `VOICE_COMMIT_TIME_CUT` (A). Largest measured gap in prod (turns that waited 3.2-4.1 s for the agent's own audio or a late live verdict); no provider cost; guarded by the false-interruption criterion above.
2. `VOICE_ELEVENLABS_PREWARM` (C). Low risk; prod shows about 130 ms connect time, so expect a small gain.
3. `ELEVENLABS_MODEL_ID=eleven_flash_v2_5` (B). Likely the largest TTS gain (about 1 s first chunk in prod), but it changes the voice: needs listening approval.
4. `VOICE_VAD_SILENCE_MS` 900, then 800 (E1, E2). Pure speed-versus-cut-off trade; accept only with zero premature answers in class 3.
5. `VOICE_TTS_PHRASE_FLUSH` (D) last: documented as needing a listening A/B.

## 7. Known risks to watch in step 1

- With no live text at commit and at least 1.5 s of speech, the agent is cut. Noise or side talk of that length that the server VAD commits would cut it (class 5 tests this). Text present at commit (noise, acknowledgements, short bursts) is not cut.
- Live deltas of the previous utterance can still leak into the next utterance's text (pre-existing). A "haan" after a real question could then look real.

## 8. Smallest useful benchmark for VOICE_COMMIT_TIME_CUT (do this BEFORE the section 2 matrix)

Question it answers: does the flag remove the 3-4 s wait that prod showed when the caller speaks over a long answer,
without cutting the agent for acknowledgements or side talk? Nothing else (model, prewarm, VAD, phrase flush) is
varied, so the other 5 matrix rows and most of the 36,000 characters are not needed to answer it.

### 8a. Needs no provider traffic (can be done now)
| Test | State |
|---|---|
| Mocked provider events: gap closed, fillers/noise/pauses/duplicates/stale partials untouched (`test_voice_end_of_turn_latency.py`, `test_voice_live_turn_ownership.py`) | done |
| Prerecorded caller audio replayed through the real `LocalVAD` only (offline): measures what the confident-speech gate does on REAL recordings of the caller's own acknowledgements, a real question, TV/side talk and room noise, so the 1.5 s / 1.0 s / 2.0 s thresholds are tuned on real audio instead of synthetic tones | needs the user to supply ~10-20 short recordings; no OpenAI or ElevenLabs calls, no cost |
| More days of prod `voice_turn_latency` / `voice_turn_trace` lines through `scripts/voice_latency_report.py` (read-only): how often turns waited on the agent's own audio, and for how long. Gives the baseline without any call. | read access to prod logs again (earlier read: 9 turns, one call) |

### 8b. Needs live provider traffic (explicit approval required, nothing started)
| Step | Provider traffic | Size |
|---|---|---|
| Live-transcriber segment check: feed 2-3 prerecorded two-utterance clips to the live transcriber ONLY and log whether the segment id changes between utterances. This decides whether the new turn-ownership rule is sound for this provider (it assumes a new segment per utterance; if one id spans utterances, the rebind fallback applies). | OpenAI transcription, no TTS, no phone | about 1 minute of audio |
| Arm A (control): one outbound call, flag off | Plivo call, OpenAI Realtime + live transcribe, ElevenLabs | about 90 s of call; about 8 agent replies |
| Arm B: same call script, only `VOICE_COMMIT_TIME_CUT=true` | same | same |
| Arm A' (repeat of A): run-to-run noise, without it a small difference cannot be read | same | same |

Scripted turns for each arm (8): 3 real questions spoken over a long (>15 s) answer; 3 acknowledgements over the agent
("haan ji", "theek hai", "hmm"); 2 stretches of side talk or TV of 2-3 s over the agent.

Record: commit-to-`response.create` (`commit_to_request`), end-of-speech to first audio sent, cuts per class, any cut on
the acknowledgement and side-talk turns, interruption latency.

Pass: real-question turns drop from the prod pattern (3.2-4.1 s of `commit_to_request`) to under 0.5 s; zero cuts on the 3
acknowledgements; side-talk cuts are counted and reported as the measured false-interruption rate (expected non-zero when
the side talk is loud; that is the risk to accept or reject), not hidden.

### 8c. Cost
ElevenLabs characters: one arm is about 8 replies x ~300 characters, fewer where the agent is cut, plus the greeting:
roughly 2,500 characters. Two arms about 5,000; three arms (with the noise repeat) about 7,500; the full matrix is
36,000. So the minimal benchmark is 14-21% of the characters, 2-3 calls of about 90 s, and about 1 minute of transcription.
Dollar cost depends on the ElevenLabs plan and OpenAI pricing, which are not known here, so none is quoted.
Preconditions to approve with it: the local `.env` shares secrets, tunnel and the prod phone number with prod (staging
isolation memory), so the run needs the pilot environment, the dial allow-list, and a single authorised number.

## 9. Revised recommendation after the ownership and threshold review

Evidence that changes the plan:
- No caller recordings exist locally. The 74 `.wav` files in `.validation-voice-ab/` are the AGENT's synthesised voice from
  the earlier voice A/B test; they contain no acknowledgements, side talk or noise, so a VAD replay on them would prove
  nothing about false cuts. The replay in 8a therefore cannot run until the user records clips (free).
- In the one prod call read, the main session's final (Whisper) transcript arrived 0.4-2.2 s after the commit, earlier than
  the live-transcriber verdicts (about 3 s). A cheap "wait for whichever transcript arrives first, up to ~1 s" variant of the
  no-text rule is therefore worth comparing against the pure duration rule. It is NOT implemented; it is a candidate arm.
- Whether live deltas carry a segment id, and whether the id changes per utterance, is unverified. The ownership fix is
  inert without ids. This is the cheapest thing to check and decides whether the fix works at all.

First live experiment (minimum scope, in this order, each needing its own approval):
1. Segment probe: 2-3 prerecorded clips (each two utterances about 2 s apart; one with a 1 s gap) fed to the live
   transcriber only. No phone, no TTS. Logs: whether `item_id` is present, whether it changes between utterances, delta
   timing relative to the audio. Stop here if ids are absent: the ownership rule needs a different key.
2. Two calls (flag off, flag on), the 8-turn script of 8b, same everything else. Add a third (flag off again) only if the
   first two differ by less than you would act on.
Do not run the section 2 matrix until step 2 shows the flag is worth keeping.

Usage and cost assumptions (all estimates; pricing not known here):
- ElevenLabs characters per arm = agent replies x average reply length. Prod logs: first-chunk character counts 104-424
  and one 28.6 s reply; at about 14 characters per second of Hindi speech that is up to about 400 characters. Assume 300
  average (range 150-400) x 8 replies, fewer where the agent is cut, plus a greeting of about 150: about 2,500 per arm
  (range 1,300-3,400). Two arms about 5,000; three about 7,500; the full matrix 36,000.
- Live transcription and Whisper: caller audio only, about 90 s per call, plus about 1 minute for the segment probe.
- OpenAI Realtime: about 90 s of session per call (audio in, text out). Plivo: about 90 s of outbound call per arm.
- Multiply these quantities by your current prices to get currency; the plan does not guess them.
