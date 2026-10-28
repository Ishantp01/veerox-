# Incident: outbound call connected, AI silent (28 Oct 2026, 11:12 UTC / 16:42 IST)

Investigation only; no call, restart, deployment, commit or spend. No numbers, ids or message content appear here.

## Which backend handled it
**The DEV stack, not staging.** The website's `NEXT_PUBLIC_API_URL` points at port 8002 (uvicorn `--reload`, main `.env`, `ENVIRONMENT=dev`, behind the ngrok tunnel). The staging API (port 8003) logged only its startup and two unsigned probes: it never saw this call. The dev stack has no pilot guard rails (no allowlist, no call cap, workers on, call recording on).

## Timeline (UTC; IST = UTC + 5:30)
| Stage | Time | Source |
|---|---|---|
| Website dial request to the dev API | about 11:12:43 | NOT OBSERVED directly (dev server logs go to its terminal only); implied by Plivo's initiation |
| Plivo call created (outbound, production number to the tester) | 11:12:44 | Plivo call record |
| Call answered | 11:12:50 | Plivo call record |
| Answer webhook served by the dev stack (XML with one `<Stream>`, keepCallAlive) | 11:12:52-11:12:53 | ngrok inspector; usage event |
| Media WebSocket connected (HTTP 101) | 11:12:53 | ngrok inspector |
| Conversation started | 11:12:54 | dev database |
| First inbound audio frame | NOT OBSERVED as a frame; inbound audio provably flowed (see next row) | no frame logging existed |
| First detected speech (first caller transcript stored) | 11:13:01 | dev database (9 caller transcripts, 3-7 characters each) |
| First OpenAI response (first agent reply stored, 120 characters) | 11:13:01 | dev database (9 agent replies, 96-154 characters each, until 11:13:30) |
| First TTS audio chunk | NOT OBSERVED | no log reachable; recording shows none reached the line |
| First outbound audio frame | NOT OBSERVED | recording shows no agent speech |
| Call ended (callee hung up, normal) | 11:13:32 | Plivo call record |
| Stream ended; 45.1 s connected stream recorded | 11:13:39 | usage event |

## Evidence summary
- The stream stayed connected for 45 s: no crash, timeout, watchdog or early hangup of the pipeline.
- Speech recognition worked (9 caller transcripts) and OpenAI generated 9 full replies.
- Plivo's recording of the call (mono mix, 40 s): nine one-second loudness bursts (the caller) separated by near-silence; no sustained speech. Each generated reply would have been 5-10 s of continuous speech. No agent audio reached the line.
- The dev `.env` configures ElevenLabs with a voice that does not exist. A rejected handshake with that voice returns `{"message": "A voice with voice_id ... does not exist.", "error": "voice_id_does_not_exist", "code": ...}` and closes with 1008.
- In our code a provider error ends the audio stream with zero chunks and the turn still completes: the text is stored and logged as if spoken (TTS recovery is off by default), so the symptom is exactly "replies in the database, silence on the line".
- The outbound greeting-first logic was on (listen-first applies to inbound only), so the agent should have greeted without being spoken to; the first stored agent reply (11:13:01) is probably that greeting, about 7 s after the stream opened.

## First failing stage and root cause
First failing stage: **text-to-speech**. Root cause (strongly supported, not proven by a log line from the call): the dev stack's `ELEVENLABS_VOICE_ID` is not a voice the account can use, so ElevenLabs refused every synthesis request. This voice id was already found invalid in earlier checks.
Not excluded by the data: frames being delivered to Plivo in an unplayable format. That is considered unlikely (the same encoding path worked on the earlier pilot call with a valid voice, and the invalid voice alone explains zero audio), but only a log from the call could rule it out.

## Diagnostics added (pilot only, flag `VOICE_PIPELINE_DIAG`, off by default)
One `voice_pipeline_turn` line per agent turn and one `voice_pipeline_call_summary` per call: frames in/out, silent turns, a short provider error code, and the first failing stage. Counts and codes only; never speech, text, numbers or ids. Inert outside `ENVIRONMENT=pilot`. Validated offline with 12 tests, including a replay of this failure with the real session class against a fake socket.
