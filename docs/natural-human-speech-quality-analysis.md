# Natural Human Speech Quality Analysis (Phase 1)

Status: analysis only. No code, config, deployment, call or provider request was made. Awaiting approval.

## 0. Read this first: limits of this analysis

* **I cannot listen to audio.** The Voice Quality Profile in section 1 is built from **signal measurements** of the reference file (loudness, pauses, pitch, rate), computed locally with numpy. Accent, pronunciation, word linking and emotion are **not measurable this way**; section 1.2 lists them as "needs a human listener" with a checklist to fill in. I have not guessed them.
* No speech-to-text was run on the reference (that would be a provider request, and no local STT is installed), so I do not know the words spoken or the language mix.
* The reference is `WhatsApp Audio 2026-10-30 at 10.53.26 AM.aac`: **45.06 s, mono, 8 kHz sample rate, AAC-LC 48 kb/s.** Narrowband and lossy. Pitch and pause numbers are reliable to roughly +/-10 to 15%; timbre and breathiness cannot be judged.
* Notably, **8 kHz is the same bandwidth as the Plivo phone line Veerox plays to**, so the reference is a fair like-for-like target for call audio.
* The reference has **no speaker identity assumed**. Nothing below identifies the speaker or a regional accent.

## 1. Reference Voice Quality Profile

### 1.1 Measured (objective)

| Property | Measurement | Notes |
|---|---|---|
| Duration | 45.1 s | |
| Pitch (F0) median | ~213 Hz | 5th to 95th percentile: 138 to 333 Hz. A higher-pitched voice; autocorrelation on 8 kHz audio can have octave errors, so treat as approximate |
| Pitch spread | std 4.8 semitones; 5th to 95th range 15.3 semitones | **Wide, lively intonation.** Typical flat TTS narration is roughly 2 to 3 semitones std |
| Phrase-final pitch | Falls 3 to 9 semitones on 5 of 6 longer phrases (one only -1.5) | Statements end with a clear downward fall; consistent with declarative/closing delivery. No rising ends in the longer runs measured |
| Loudness variation | std 8.8 dB within speech | Strong dynamic emphasis between stressed and unstressed syllables |
| Syllable-like energy peaks | ~4.8 per second of speech (~3.3 per second overall) | Rough proxy for rate. Indicates a relaxed to moderate conversational pace, not rapid |
| Speech vs. silence | ~69% of frames above the speech threshold | So ~31% is pauses/low-energy gaps |
| Pauses >= 0.3 s | 15 in 45 s (about one every 3 s) | Distribution of the 19 pauses >= 0.15 s: many short 0.15 to 0.45 s, a few 0.5 to 1.0 s, one 1.6 s |
| Long pauses | 7 pauses >= 0.6 s, 1 >= 1.0 s (1.59 s) | Thinking/turn-boundary pauses are real and unevenly spaced |

### 1.2 Needs a human listener (not measurable by me)

Please listen and fill this in; it is the part that matters most for "accent" and "natural delivery", and the plan below depends on it.

| Item | What to note |
|---|---|
| Accent and pronunciation | Which sounds differ from the current agent (e.g. retroflex ट/ड, ड़/ढ़, v/w, "z" in ज़/फ़, English words in Indian vs. neutral accent)? Do not need a region name, just the audible differences |
| Connected speech / word linking | Are words run together ("kya hua" as "kyahua"), are final vowels dropped or shortened, is "hai/hain/hoon" reduced to a weak sound? |
| Language mix | Hindi / English / Hinglish proportion, and how English words are pronounced inside Hindi sentences |
| Question delivery | Does pitch rise at the end, or does the question stay level/fall with a particle ("na?", "hai na?") carrying the question? |
| Greeting vs. explanation vs. acknowledgement | Typically greeting = brighter and higher with a drawn-out vowel; explanation = steadier with emphasis on key words; acknowledgement ("haan", "achha", "ji") = short, low energy, often falling. Confirm which of these holds |
| Breathing / filler | Audible inhales, "hmm", "aa", self-corrections, laughter? |
| Emotion | Warm, amused, serious, rushed? Does it shift between phrases? |

I can supply per-phrase timestamps (pitch, pauses) to speed up this listening pass if useful.

### 1.3 What the numbers imply for design

Targets worth testing against, if the listening pass confirms them: **wide pitch movement (about 4 to 5 semitones std), clear fall at statement ends, strong loudness contrast between stressed and unstressed words, and uneven pauses (short micro-pauses most of the time, an occasional 0.5 to 1.5 s)**. The current output is not measured here (no paid audio is allowed), so the gap in section 3 is inferred from configuration, not from comparing waveforms. Once approved, one controlled listening round with these targets will confirm it.

## 2. Current Veerox implementation findings

### 2.1 Voice path

OpenAI Realtime does speech-to-text, reasoning, tool calls and turn detection in **text-only output** mode (`openai_realtime_model = gpt-4o-realtime-preview`). The reply text is streamed over a WebSocket to **ElevenLabs** (`wss://.../stream-input`) which returns **`ulaw_8000`** audio sent to Plivo. See [elevenlabs_client.py](../apps/api/channels/voice/elevenlabs_client.py) and [adapter.py](../apps/api/channels/voice/adapter.py).

### 2.2 TTS model and voice

* **Model: `eleven_flash_v2_5`** (default, [config.py:365](../apps/api/config.py#L365), [voice_profile.py:71](../apps/api/channels/voice/voice_profile.py#L71)). Lowest latency, lowest expressiveness. It does **not accept `style`**; the code only forwards `style` for `eleven_multilingual_v2` ([voice_profile.py:53](../apps/api/channels/voice/voice_profile.py#L53)).
* Allowed models: flash v2.5, turbo v2.5, multilingual v2 only. **`eleven_v3` / any newer expressive model is not allowed** ([voice_profile.py:47](../apps/api/channels/voice/voice_profile.py#L47)).
* **Voice:** platform default `cRtvZrGFUW6HAyaDM8xe` ("Reyaansh", candidate A5), config fallback `CwhRBWXzGAHq8TQ4Fs17`. Voice selection depends on env/profile; the pilot template only has a `<voice id>` placeholder. I have not verified which is live, and its pitch range is unknown to me. The reference is a high-pitched voice (median ~213 Hz); a mismatch in pitch register would be audible.
* **`language_code` is deliberately not sent**; `pronunciation_dictionary_locators` is also not sent ([voice_profile.py:26](../apps/api/channels/voice/voice_profile.py#L26)). The model therefore guesses language per phrase from the text.

### 2.3 Voice settings (what is actually sent)

* Legacy/no-profile default: **`stability 0.45`, `similarity_boost 0.80`**, nothing else ([voice_profile.py:103](../apps/api/channels/voice/voice_profile.py#L103)). `speed` and `use_speaker_boost` are only sent when an org sets them.
* Dashboard: settings live in `Org.voice_profile` (JSON), edited through `GET/PUT /admin/settings/voice-profile` ([admin.py:2879](../apps/api/routers/admin.py#L2879)). **I found no screen in `apps/web` for it**, so it appears to be API-only today. Please confirm; if so, any tuning needs the API or a new UI.
* Earlier candidates in [pilot-8l-voice-candidates.json](pilot-8l-voice-candidates.json) were prepared but **none was rated by a person** ([pilot-8l-latency-and-naturalness.md](pilot-8l-latency-and-naturalness.md) line 71), so there is no listening evidence for any setting yet.

### 2.4 Text preprocessing and pronunciation

* [tts_normalize.py](../apps/api/channels/voice/tts_normalize.py) turns numbers, rupees, dates, times, phone numbers, OTPs and a few abbreviations into spoken words and applies an org pronunciation dictionary. It is **off unless the org profile sets `normalize_speech`**. It handles numbers well but does nothing about **accent, vowel length, retroflex sounds, English-in-Hindi pronunciation or prosody**.
* Language is detected per chunk using a fixed list of Hinglish marker words; Devanagari triggers Hindi words. Romanised Hindi is passed through as Latin text, so the model reads it with English letter-to-sound rules unless it recognises the words. That is a known weak point for Hinglish on flash models (the project's own notes list "Roman-script Hindi through the flash model" as the first suspected cause, 8l doc line 70).
* Abbreviations are spelled letter by letter (`GST` to `G S T`), fine for English, but there is no Indian-English pronunciation handling.

### 2.5 Text generation (Hindi/English/Hinglish)

* The voice prompt tail is in [prompts.py:195-336](../apps/api/core/prompts.py#L195): `VOICE_SPEAKING_STYLE`, `VOICE_NATURAL_HINGLISH`, `VOICE_HUMAN_STYLE`/`_V2`, plus tone. All are **opt-in and default off** (`voice_human_style=False`, `speaking_style`/`natural_hinglish` per org).
* The prompts steer *wording* toward short everyday Hinglish and say "do not write fillers like umm". They **forbid** filler/breath/laughter text, which are exactly the cues that make speech sound human.
* The model writing the text is `gpt-4o-realtime-preview` in text mode. Whether it outputs Devanagari or Roman script for Hindi is not controlled anywhere I found; this directly decides how ElevenLabs pronounces it.
* No prosodic markup (commas/ellipses/dashes for pausing) is added beyond the prompt's suggestion.

### 2.6 Chunking and streaming

* `voice_tts_phrase_flush` is **False by default** ([config.py](../apps/api/config.py)). Without it, text deltas of a few characters go straight to ElevenLabs without `flush`, and the provider generates when its `chunk_length_schedule` fills (default `[120,160,250,290]` characters). Small early chunks mean the model hears little context, which hurts intonation, especially the sentence-final fall and question rise.
* When phrase flush is on, [PhraseBuffer](../apps/api/channels/voice/elevenlabs_client.py#L160) releases whole sentences (min 12 characters; commas only after 40 characters) with `flush: true`. That is better for prosody but **each `flush` forces generation with no look-ahead**, so a sentence is spoken without knowing what follows (unavoidable on streaming).
* `chunk_schedule` is only sent when set (`voice_tts_chunk_schedule` empty).
* **One new WebSocket session per assistant turn** (not reused). The model gets no previous text, so there is **no continuity across turns** (`previous_text`/`next_text` style context is not used); every reply starts "cold", which tends to make each turn sound like a fresh announcement rather than a continuing conversation.

### 2.7 Audio delivery

* **ulaw 8 kHz end to end.** This is a hard telephony limit (the same as the reference's 8 kHz), so band-limiting is not the gap. But mu-law quantisation plus no post-processing (no loudness leveling) is.
* Barge-in clears audio via `clearAudio`; a short cached "ji"/"haan" ack clip exists behind a flag ([instant_ack.py](../apps/api/channels/voice/instant_ack.py), off by default).

### 2.8 Voice preview

* [voice_preview.py](../apps/api/channels/voice/voice_preview.py) uses the **same streaming session** as live calls, and outputs **8 kHz mu-law WAV**. Good for call realism, but it means previews cannot show what the voice is capable of at full bandwidth, and there is no A/B or rating store; 8l candidates were never scored.
* Preview phrases ([preview_phrases.json](../apps/api/channels/voice/preview_phrases.json)) are mostly single short sentences (greeting, confirm, numbers). They do not test **questions, back-channels, mid-sentence English, long explanations, or multi-sentence continuity**, which is where naturalness differs most.

## 3. Why the current output likely sounds less natural (hypotheses, not yet verified by listening)

Ranked by my confidence:

1. **Model tier.** Flash v2.5 is optimised for latency and is the least expressive option; the project's own notes name it as a prime suspect. Expect flatter pitch than the reference's ~4.8 semitone spread.
2. **Roman-script Hindi.** If the LLM writes Hindi in Latin letters, pronunciation and rhythm are approximated by English-leaning rules: wrong vowel length, no retroflex, English stress. This is the most likely cause of the *accent* gap.
3. **Cold, short chunks.** No phrase flush by default, no cross-turn context, so each fragment is rendered with little context; intonation (rises/falls) is the first casualty.
4. **Prompt discourages human cues.** The style prompts remove fillers, breath, hesitation and uneven pacing, and are off by default. The reference has uneven pauses (0.15 s to 1.6 s) and wide loudness contrast; written text rarely carries those.
5. **Settings tuned for stability, untested for expression.** 0.45/0.80 with no `style`/`speed`. The baseline was never listened to against alternatives (no ratings exist).
6. **Voice choice.** Unknown match to the reference's register (F0 ~140 to 330 Hz).
7. **Telephone mu-law path.** Real but shared with the reference, so not the differentiator.

## 4. Prioritised improvement plan

Nothing here is applied. "Effort" is rough dev effort. Where a change needs paid audio to judge, the plan says so. All changes should go behind flags, default off, and be A/B listened to before use.

### A. LLM dialogue improvements

| # | Recommendation | Expected benefit | Effort | Possible side effects |
|---|---|---|---|---|
| A1 | **Force a consistent Hindi script policy**: Devanagari for Hindi words, Latin only for true English words (and test both against the voice). Add to the voice prompt. | Largest accent gain if listening confirms Roman-script is the issue; correct vowel length and retroflex sounds | Low (prompt) | Mixed-script text can make flash/turbo switch language mid-phrase; needs listening; transcripts and any text-based logic will show Devanagari |
| A2 | Allow **pre-approved, spoken-safe discourse markers** ("haan", "achha", "hmm,", "dekhiye") written as ordinary words, and shorter fragments for acknowledgements; keep the ban on literal stage directions | More human rhythm and acknowledgement delivery | Low | Overuse sounds scripted; existing no-repeat rules should stay; must not delay the AI disclosure |
| A3 | **Punctuation for prosody**: instruct questions to end with "?", statements with "." or "।", and use commas/ellipsis/dashes only for deliberate short pauses | Better rise/fall; more varied pauses | Low | Models over-punctuate; must be capped; ellipsis can be read as hesitation too long |
| A4 | Vary utterance length (1 short + 1 longer sentence) instead of uniform short sentences | Less metronomic rhythm | Low | Longer sentences raise latency to first audio |
| A5 | Enable the existing `speaking_style`, `natural_hinglish`, `human_style` blocks in a **test org only** and compare | Uses work already built | Very low | Interacts with script authority rules; test with real scripts |

### B. TTS model and voice improvements

| # | Recommendation | Expected benefit | Effort | Possible side effects |
|---|---|---|---|---|
| B1 | **Listening A/B of existing candidates** (the 8l file: turbo, multilingual v2, other voices) with the reference playing beside them, rated by a person | The first real evidence; cheapest way to choose | Low (~5,200 chars of paid synthesis, needs your approval) | Cost; ratings are subjective |
| B2 | Move default to **turbo v2.5** (or multilingual v2 for non-live/greeting audio) if B1 shows a clear gain | Better expressiveness and pronunciation | Low | Higher latency and cost; multilingual v2 is likely too slow live |
| B3 | **Evaluate a newer expressive ElevenLabs model** (e.g. v3-class, if available for streaming) by adding it to the allow-list | Largest potential naturalness jump; supports audio tags | Medium | Streaming/latency support may be limited; I have not verified current model availability; needs confirming against ElevenLabs docs |
| B4 | **Choose a voice matched to the reference's register and pace** (audition several Indian-language voices; consider Professional Voice Clone only with consent of the speaker) | Closest match to target | Medium | Voice cloning requires explicit permission from the person recorded; do not clone this recording without it |
| B5 | Pre-generate and **cache fixed phrases** (greeting, common acknowledgements) with the best model (the greeting-cache flag exists) | Studio-quality common lines at zero latency | Low | Cached lines can sound different from live speech; needs consistency check |

### C. Pronunciation and accent improvements

| # | Recommendation | Expected benefit | Effort | Possible side effects |
|---|---|---|---|---|
| C1 | **Send `language_code`** (supported on some models) per call/phrase | Stops wrong-language guesses | Low | Wrong code harms mixed text; currently excluded on purpose, so verify model support first |
| C2 | Extend the pronunciation dictionary with the client's names/brands as **phonetic respellings** in Devanagari; turn on `normalize_speech` in the test org | Fixes names and numbers | Low | Dictionary needs upkeep; whole-word matching only |
| C3 | Use ElevenLabs **pronunciation dictionary locators** (alias or phoneme rules) | Precise control of recurring words | Medium | More provider calls and state; IPA support varies by model |
| C4 | Add a small **English-word-in-Hindi handling** layer (Hinglish loanwords written Devanagari: "अपॉइंटमेंट") | More natural code-switching | Medium | Needs a vetted word list; wrong transliteration sounds worse than English |
| C5 | Build a **pronunciation test set** from the reference's actual vocabulary once it is transcribed | Measurable accent progress | Medium | Needs a transcript (human or approved STT) |

### D. Prosody and voice settings

| # | Recommendation | Expected benefit | Effort | Possible side effects |
|---|---|---|---|---|
| D1 | **Do not change stability/similarity/style blindly.** Run a small grid on a fixed phrase set, rated by listeners, and fix the sweep points beforehand | Evidence-based tuning | Low-Medium | Lower stability gives more expression but also artifacts and voice drift; style raises latency and is only valid on multilingual v2 |
| D2 | Test `speed` 0.9 to 1.05 against the reference's pace (~3.3 syllable-peaks/s overall) | Matching the reference rhythm | Very low | Slower speed raises call duration and cost |
| D3 | Test `use_speaker_boost` on/off | Clearer identity; small effect | Very low | Higher latency |
| D4 | Add richer preview phrases (questions, acknowledgements, long explanation, Hinglish switch, multi-turn) | Previews that actually reveal naturalness gaps | Low | Slightly more preview spend |

### E. Streaming and audio-delivery improvements

| # | Recommendation | Expected benefit | Effort | Possible side effects |
|---|---|---|---|---|
| E1 | **Enable `voice_tts_phrase_flush`** (test org) so whole phrases are spoken with context | Better intonation, especially sentence ends and questions | Very low (flag exists) | Slightly later first audio; per-flush generation with no look-ahead |
| E2 | Tune `chunk_length_schedule` (e.g. larger first chunk) after E1 | Trade latency vs. context | Low | Higher first-audio latency; the provider warns small values cost quality |
| E3 | **Cross-turn context**: send the previous assistant text with each new session where the API supports it | Turns sound continuous instead of re-announced | Medium | Support varies by model/endpoint; verify before building |
| E4 | Consider the **non-streaming/HTTP endpoint with `previous_text`/`next_text`** for non-latency-critical lines | Highest quality for fixed lines | Medium | Higher latency; only suitable for greetings/closings |
| E5 | **Loudness leveling** of the TTS output (gentle gain normalisation before mu-law) | More consistent volume on the phone | Low | Pumping if over-compressed; risk of clipping |
| E6 | Keep mu-law 8 kHz (no change possible on PSTN); verify any future SIP/WebSocket provider (see the VoiceLink thread) supports it or higher | Avoid regression | None | n/a |

### Suggested order

1. **You listen to the reference and fill 1.2** (30 minutes, free). This decides A1/C1.
2. **Free changes first (no provider calls):** prompt work A1 to A5 and flag-only E1, in a test org.
3. **One approved paid listening round (B1 + D2 + D4):** about 5,200 characters, rated blind by you and the client. Decide model, voice and speed from that.
4. Then C-series pronunciation fixes and the E3/E4 work only if still needed.

## 5. Open questions for you

1. Which voice ID and model are **live in production/pilot right now**? The env template only has a placeholder; I did not inspect production.
2. Is the reference speaker aware their audio is being used as a target? (Matters only if cloning is ever considered, which I do not recommend without their consent.)
3. Is the language of the reference Hindi, English or Hinglish, and are calls the same? If you can share a transcript or the intended text, I can build a matched test set.
4. Do you approve one capped paid listening round (suggest <= 6,000 characters)?
5. Latency budget: how much extra delay (ms) is acceptable for better quality?

## 6. How the reference was measured (reproducible, local)

`ffmpeg` converted the AAC to 16 kHz mono WAV; numpy/scipy computed 25 ms RMS energy, energy-gated speech and pause runs, a rough syllable-peak count and autocorrelation pitch (70 to 400 Hz). Script and venv are in the session scratchpad, not in the repo. These are rough signal statistics, not a linguistic analysis.
