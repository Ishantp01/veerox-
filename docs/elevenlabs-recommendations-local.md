# ElevenLabs support recommendations: local implementation notes (28 Oct 2026)

Local only. Nothing deployed, committed, pushed or called; no paid audio generated; no production setting touched. Everything below was checked in code and tests unless marked UNVERIFIED.

## What exists today
| Item | Current |
|---|---|
| WebSocket | `wss://api.elevenlabs.io/v1/text-to-speech/{voice}/stream-input?model_id={model}&output_format=ulaw_8000`, one socket per assistant turn (`ElevenLabsTTSSession`) |
| Model | `ELEVENLABS_MODEL_ID` (dev `.env`: `eleven_multilingual_v2`; `.env.pilot`: `eleven_turbo_v2_5`); an org voice profile's `model_id` overrides it |
| Voice settings | no profile: stability 0.45, similarity 0.80, plus style 0.15 on multilingual_v2; `use_speaker_boost` / `speed` not sent. Profile: all five configurable and validated |
| Phone leg | Plivo `<Stream contentType="audio/x-mulaw;rate=8000">`; frames sent back as `playAudio` mu-law 8000 |
| VAD | OpenAI server_vad, `VOICE_VAD_SILENCE_MS` (default 1100), `create_response` / `interrupt_response` off; app decides replies/cuts. Flags `VOICE_SMART_TURN_TAKING`, `VOICE_LOCAL_VAD`, `VOICE_ADAPTIVE_ENDPOINTING`, all default off |
| Transcription | two paths, see below |

## Implemented (flag/config-gated, tested)
1. **Chunk schedule, platform default.** The org profile's `chunk_schedule` already reached `generation_config.chunk_length_schedule`. Added `VOICE_TTS_CHUNK_SCHEDULE` (e.g. `50,120,160`) for calls whose org sets none. Same documented limits as the profile (1-6 integers, each 50-500, from the provider's schema); invalid values are ignored, so the handshake stays unchanged. Default empty = provider default `[120,160,250,290]`. Tests: handshake with/without, org wins, 8 invalid shapes ignored. Existing metrics already record `tts_chars_at_first_chunk`, `tts_send_to_audio_ms` and `tts_connect_ms`; compare them between schedules.
2. **Prewarm repair** (`VOICE_ELEVENLABS_PREWARM`, still default off). The socket is built from the call's own voice profile (no cross-call sharing; it lives on that call's state). Gap found and fixed: a prewarmed socket was spoken into even if idle past the provider's 20 s `inactivity_timeout` or already rejected. It is now closed and replaced by a fresh connection (log `elevenlabs_prewarm_stale`). Tests: stale age, already-failed, fresh socket still reused; existing tests cover discard on tool-call turns, cancel while connecting, failed prewarm fallback and cleanup at call end. **Benefit unmeasured:** the 330 ms figure is not assumed; the pipeline logs `tts_connect_wait` per turn, compare prewarm on/off in a supervised local call.
3. **VAD thresholds.** Already configurable. Tests now prove 650/800/900/1100 reach the realtime session, with app-controlled response and interruption.
4. **Filler / acknowledgement / interruption regression tests** pinning today's behaviour (uh/um/hmm are fillers; okay/yes/right/haan are acknowledgements answered without a hold; real questions and "wait/stop" are real turns).
5. **Audio contract tests**: Plivo frames are mu-law 8000 matching the stream element; the only format requested from ElevenLabs is `ulaw_8000`.

## Evaluated, not implemented
- **Model / format.** The account's catalogue (`/v1/models`) lists `eleven_v4`, `eleven_v4_turbo`, `eleven_v3` with no style or speaker boost, and the docs describe the WebSocket endpoint's `model_id` default as `eleven_multilingual_v2`. No evidence that v3/v4 stream over `stream-input` (UNVERIFIED without a paid test), so they are not wired in. Model is already configurable per org.
- **Higher-quality output format.** The call leg is 8 kHz mu-law end to end (the Plivo `<Stream>` element and the OpenAI input are both G.711). A `pcm_16000` or `mp3` reply would need a different Stream contentType and local transcoding down to 8 kHz anyway; the phone network is the ceiling. Not added: it would be configuration with no audible gain. `ulaw_8000` stays the only requested format (test-pinned), which is also the fallback.
- **Stability 0.35 vs 0.45 and the omitted `use_speaker_boost` / `speed`.** Already settable via the org profile; no default changed. Needs your listening test (`scripts/voice_consistency_samples.py`, awaiting your approval for ~528 credits).
- **Devanagari conversion for Hindi.** Veerox's agent writes romanised Hinglish. A rule-based Roman-to-Devanagari converter cannot tell Hindi from English words or names without a dictionary or language model; any attempt would mangle names, URLs and English terms, and a model-based converter is a second paid service which you did not approve. Not implemented. The existing `TTSNormalizer` (opt-in per org: numbers, pronunciations) is unchanged. If you want this, the safe option is to have the realtime model itself emit Devanagari for Hindi sentences via the prompt (no extra service), tested on A/B samples first.
- **`VOICE_SMART_TURN_TAKING` / `VOICE_LOCAL_VAD`.** Left as implemented and off; no change was needed, and each still needs live-call validation.

## Duplicate transcription (analysis only; nothing disabled)
| Path | Purpose | Cost note |
|---|---|---|
| `whisper-1` inside the OpenAI Realtime session (`input_audio_transcription`, language `hi`) | The authoritative transcript: stored in the conversation, shown in the dashboard, given to the model as the user turn. Arrives after the turn ends | billed per audio minute by OpenAI |
| `gpt-live-transcribe` second connection (`LiveTranscriber`, only when `VOICE_LIVE_TURN_DETECTION` is on) | Streaming partials while the caller is still speaking, used only for turn-taking (filler vs real, hold, barge-in). Never stored | a second audio stream of the same call |
The two are not substitutes: whisper-1 is too late for turn-taking and the live partials are not the stored transcript. No cost figures are recorded in the project, so none are claimed. Safe local change (not applied): when `VOICE_SMART_TURN_TAKING` and `VOICE_ADAPTIVE_ENDPOINTING` are both off, nothing consumes the partials except the filler/barge-in judge; test turning the live connection off only with `VOICE_LIVE_TURN_DETECTION=false` (existing kill switch) and compare cut-offs in supervised calls first.

## Needs manual verification
Whether v3/v4 stream; real time-to-first-audio with `50,120,160` vs default; prewarm gain; 800/900 ms silence against real callers; listening comparison of stability and model.

## Local comparison procedure (no calls, no spend until you approve)
1. Baseline: current `.env`, unchanged. 2. Variant: set only `VOICE_TTS_CHUNK_SCHEDULE=50,120,160` (or `VOICE_VAD_SILENCE_MS=800`) in the dev `.env`; the dev API reloads. 3. Run the same scripted web-test conversation, compare `tts_chars_at_first_chunk`, `tts_send_to_audio_ms` and `tts_connect_wait` from `scripts/voice_latency_report.py`. 4. Revert the line.
