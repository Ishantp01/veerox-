# Same voice, different sound: findings and controlled comparison plan (28 Oct 2026)

Investigation only. No audio was generated, no call placed, no voice id, configuration or production setting changed. The measurements below use files that already existed (earlier approved samples) and free provider lookups. Voice under test: `VbDz3Q…kfwE` ("Anant - Realistic BFSI Customer Care", professional, Hindi).

## 1. Findings

| Question | Finding | Evidence |
|---|---|---|
| Model used by the website preview | **Not discoverable.** The API exposes the preview file (a 44.1 kHz, 128 kbit/s MP3) but not the model or text used. The website's Speech page uses whichever model is selected there. The account has newer models than Veerox uses (`eleven_v4`, `eleven_v4_turbo`, `eleven_v3`, `eleven_v3_conversational`), and this voice is fine-tuned separately for each model | `/v1/models`, voice object |
| Model used by Veerox | `eleven_multilingual_v2` on the dev stack (`ELEVENLABS_MODEL_ID`); the pilot file uses `eleven_turbo_v2_5`. Streaming (WebSocket) supports the v2 / v2.5 families, not v3 / v4 | effective settings; models docs |
| Settings Veerox sends (no org profile; all 5 dev orgs have none) | `stability 0.45`, `similarity_boost 0.80`, `style 0.15` (multilingual_v2 only). **`use_speaker_boost` and `speed` are not sent** | `voice_profile.LEGACY_VOICE_SETTINGS`, `DEFAULT_STYLE`; effective-settings script |
| The voice's own stored settings (what the website applies by default) | `stability 0.65`, `similarity_boost 0.30`, `style 0.0`, `use_speaker_boost true`, `speed 1.05` | `GET /v1/voices/{id}/settings` |
| Do the settings differ | **Yes, every one.** Stability 0.45 vs 0.65, similarity 0.80 vs 0.30 (a large gap for a professionally fine-tuned voice), style 0.15 vs 0, speaker boost and speed 1.05 not sent. ElevenLabs does not document what a request's omitted fields fall back to (stored value or the generic default: boost true, speed 1.0), so the omitted two are unverified | API docs; the plan's B0 sample tests it |
| Text chunking and streaming | One new WebSocket per reply, no context from the previous reply; text is sent as the model's small token pieces; with `VOICE_TTS_PHRASE_FLUSH` off (current) ElevenLabs buffers by its default schedule (about 120 characters before the first audio). No `previous_text`, no language code (not supported on multilingual_v2) | `elevenlabs_client.py`, adapter |
| Text the voice receives | The agent writes romanised Hinglish; the website preview text is typically Devanagari Hindi. Spelling alone changes accent and rhythm | prompts; preview sample |
| Audio format and conversion | Requested as `ulaw_8000` (8 kHz, mono, G.711 mu-law) **from ElevenLabs directly**; Veerox decodes nothing, resamples nothing, re-encodes nothing: the provider's bytes are base64-wrapped and sent to Plivo (`playAudio`, `audio/x-mulaw`, 8000). Asserted by an offline test | `_send_media`; `test_dev_voice_safety.py` |
| Different provider, voice, model or config than expected | **No.** TTS provider and engine are ElevenLabs, voice and model match the running process (`/admin/settings/voice-profile`), no shell variable overrides them, no org profile overrides them. The only mismatch is with the website, not inside Veerox | effective settings; running-API check |

## 2. Where the sound first changes
1. **At synthesis (earliest, and the largest controllable factor): the settings and the model.** Veerox asks for a different voice rendering than the website does (section 1). This happens before any audio format or phone network.
2. **At the output format, still inside ElevenLabs.** Measured on an identical sentence and voice from earlier samples: full band (24 kHz) vs the Veerox phone path (8 kHz mu-law):

| File | Spectral centroid | 95% rolloff | Energy above 4 kHz |
|---|---|---|---|
| Full-band | about 2,540-2,610 Hz | about 4,800-5,000 Hz | 3.1-3.9% |
| Veerox phone path | about 1,160-1,190 Hz | about 2,350-2,430 Hz | 0% |

   The brightness measure halves and nothing above 4 kHz survives (sibilants, "air", part of the voice's character). This is inherent to telephony; it is in the audio before Plivo.
3. **Plivo and the phone network (not controllable):** Veerox adds nothing between ElevenLabs and Plivo. The carrier may transcode further (mobile networks often narrow to 300-3,400 Hz).

## 3. Controlled comparison (one short sentence, one voice, one model)
Test sentence (88 characters, Roman script, the agent's real style): "Namaste, main Veerox ka AI assistant hoon. Kya main aapse do minute baat kar sakta hoon?" The Devanagari spelling is available as `--script devanagari` for a second pass.

| Stage | Sample | How it is produced | Cost |
|---|---|---|---|
| A | Website preview | **You:** on the ElevenLabs site, select this voice and the same model as Veerox (Eleven Multilingual v2), paste the sentence, leave the sliders at the voice's defaults, play it and record the system audio (or download); then repeat with the newest model to see the gap | none |
| B0 | API, settings omitted | script | 88 chars |
| B1 | API, voice's stored settings, full band | script | 88 |
| B2 | API, Veerox's settings, full band | script | 88 |
| B3 | API, stored settings, 8 kHz mu-law | script | 88 |
| C1 | Veerox pipeline as it runs today (what Plivo receives) | script (real session class) | 88 |
| C2 | Veerox pipeline with the stored settings (the proposed fix) | script | 88 |
| D | Heard on the phone | **You:** after approval, one supervised call (or play C1/C2 through the same route); record the call on a second device | one call |

**Cost to approve:** 6 generations x 88 = **528 characters, about 528 credits** (multilingual_v2 is 1 credit per character; roughly $0.05 at third-party list prices). Approve with: `python scripts\voice_consistency_samples.py --run --max-chars 700`. Nothing is generated until you say so. A second pass with the Devanagari spelling is another 528 (or fewer, the sentence is 79 characters).

**What each pair isolates**
- B0 vs B1: whether omitting settings gives the stored values (measure duration: speed 1.05 vs 1.0 differs by about 5%).
- B1 vs B2: the effect of Veerox's three differing settings, same format.
- B2 vs C1 (and B1 vs B3): the effect of the 8 kHz phone format alone.
- C1 vs C2: the effect of the proposed fix through the real pipeline.
- A vs B1: whether the website used the same model (a mismatch that no setting can close).

**Measure objectively (free):** `python scripts\voice_audio_metrics.py <files>` prints loudness, peak, spectral centroid, 95% rolloff and energy per band. **Then listen blind:** rate B1, B2, C1, C2 (and A for reference) for similarity to the website voice, naturalness and pronunciation, using `scripts\voice_ab_samples.py`'s blind page pattern. The numbers describe bandwidth and loudness; they cannot judge identity or accent.

## 4. Smallest fix (to be validated by the samples above before it is applied)
**Set the org's voice profile to the voice's own stored settings.** It needs no code change: the existing voice-profile validation accepts it (checked), and it takes effect from the org's next call.
```
PUT /admin/settings/voice-profile   (org admin)
{"profile": {"model_id": "eleven_multilingual_v2", "stability": 0.65, "similarity_boost": 0.3,
             "style": 0.0, "use_speaker_boost": true, "speed": 1.05}}
```
Latency impact: none from stability or similarity; `style 0` is cheaper than 0.15; speaker boost adds a small provider-side latency (documented); speed 1.05 shortens audio slightly. Measure time-to-first-audio on C1 vs C2 (the script's session path) before adopting. If the boost costs too much latency, send `use_speaker_boost: false` and re-listen.
Follow-ups, only if the listening test shows they matter: (a) the phone format is a hard ceiling, so choose the model/settings that sound best at 8 kHz rather than best full-band (flash/turbo are faster, multilingual_v2 usually richer); (b) feed the model's text to the voice in the script the voice was tuned for (Devanagari for Hindi) or normalise numbers and names; (c) smaller first phrase with `VOICE_TTS_PHRASE_FLUSH` only after it has been rated; (d) matching the website exactly may be impossible if it used a v3/v4 model, which streaming does not support.

## 5. Not changed, and open items
Voice id, models, production, deployments, calls: unchanged and unrun. Open: which model the website preview used (ask whoever recorded it, or record A at several models); whether omitted settings fall back to stored values (B0 answers it); listener ratings, which only you can supply.
