# Natural Speech Quality, Phase 2: implementation and offline review

> **Phase 3 update:** the unpunctuated-text hold (section 7), the settings-endpoint `style` display mismatch (section 4) and the missing listening-test runner (section 9, item 3) are addressed in [natural-speech-phase3.md](natural-speech-phase3.md). The text below is the Phase 2 record and is left as written, except for the status notes in section 10.

Status: implemented behind per-org opt-in fields, reviewed and tested offline. **Nothing is enabled anywhere.** The pilot organisation's voice profile is still empty, and production and pilot configuration are unchanged.

## 0. Confirmations (what did and did not happen)

| Item | Status |
|---|---|
| OpenAI, ElevenLabs or Plivo API requests | **None.** Tests use fakes; the scripts I wrote make no network calls |
| Paid audio generated | **None** |
| Calls placed | **None** |
| New voice-profile fields enabled on any org | **None** |
| Production or pilot configuration modified | **None** (read-only: production `.env` was grepped over SSH for voice id/model only in the previous round; the pilot DB was read with one `SELECT`; nothing written) |
| Deployed, committed or pushed | **No.** All changes are uncommitted in the working tree |
| Credentials exposed | **No** |
| Other web lookups | Public ElevenLabs/pricing documentation search results only; no API calls |
| Local environment change | `pip install "fakeredis[lua]"` into the local Python, because the project's own `pyproject.toml` (line 42) declares it as a test dependency and it was missing. No project file changed |

## 1. Full backend test results

Command: `python -m pytest apps -q -p no:cacheprovider -rs`, run once on the final code (after the last code, prompt and test edits).

| Passed | Failed | Skipped | Errors | Warnings | Duration |
|---|---|---|---|---|---|
| **2802** | **0** | **0** | **0** | 8 | **845.74 s (14 min 05 s)** |

* The 8 warnings are FastAPI "Duplicate Operation ID" notices for routes in `channels/voice/webhook.py`, a file Phase 2 did not touch.
* The first full run (before the final edits) did not run any test: collection stopped on `test_pilot_8j_replay.py` because `fakeredis`, a dependency declared in `pyproject.toml`, was not installed locally. After installing it, a run was started and then deliberately stopped when I changed the prompt wording and added tests, so only the final run above counts.
* The earlier targeted shards (442 tests) were a subset of this run and are superseded by it.
* This run includes the 30 Phase 2 tests: defaults off, fingerprint unchanged, invalid values ignored, byte-identical prompt when off, org isolation, no unsupported provider fields sent, phrase boundary cases, incomplete phrase flushed at finish, script preservation, and job-list/character-count accuracy.
* Not run: any test that needs a real provider, database server or phone call; none exist in the suite (it uses fakes).

## 2. Files changed and purpose

All uncommitted. Every behavioural change is inert until an org's `voice_profile` sets a new field.

| File | Purpose |
|---|---|
| [apps/api/core/prompts.py](../apps/api/core/prompts.py) | New prompt blocks `VOICE_NATURAL_SPEECH`, `VOICE_SCRIPT_DEVANAGARI`, `VOICE_SCRIPT_ROMAN`; `render_voice_append(natural_speech=False, hindi_script=None)`. Output is byte-identical to before when both are off |
| [apps/api/channels/voice/voice_profile.py](../apps/api/channels/voice/voice_profile.py) | Five optional profile fields (`natural_speech`, `hindi_script`, `phrase_flush`, `phrase_min_chars`, `phrase_soft_chars`), matching `ResolvedVoice` fields, `prompt_extras()`. Fingerprint only changes if a phrase option is set |
| [apps/api/channels/voice/realtime_bridge.py](../apps/api/channels/voice/realtime_bridge.py) | Reads the calling org's `prompt_extras` and passes them to the prompt |
| [apps/api/channels/voice/elevenlabs_client.py](../apps/api/channels/voice/elevenlabs_client.py) | `PhraseBuffer(min_chars, soft_chars)` (defaults = old constants); per-org `phrase_flush` override of `VOICE_TTS_PHRASE_FLUSH` |
| [apps/api/channels/voice/voice_preview.py](../apps/api/channels/voice/voice_preview.py) | `load_phrases(path)` and `synthesize(stream_words=)`, defaults unchanged |
| [scripts/voice_compare.py](../scripts/voice_compare.py) | Optional `--phrases-file`, `--stream-words` |
| [scripts/natural_speech_sheet.py](../scripts/natural_speech_sheet.py) (new) | Offline sheet, character/cost estimate, buffering simulation, reproducible blind job list |
| [docs/natural-speech-eval/phrases.json](natural-speech-eval/phrases.json), [candidates.json](natural-speech-eval/candidates.json) (new) | The listening scripts (11 Roman/Devanagari pairs + 1 English) and the four candidates |
| [apps/api/tests/test_natural_speech_phase2.py](../apps/api/tests/test_natural_speech_phase2.py) (new) | 30 tests |
| [docs/natural-human-speech-quality-analysis.md](natural-human-speech-quality-analysis.md) (new, Phase 1) | Reference analysis and plan |

One change made during this review: the `VOICE_NATURAL_SPEECH` line "do not introduce yourself again once you have" now reads "once the caller has heard it (if an instruction says they did not hear it, say it as instructed)". The original wording could have conflicted with `GREETING_CUT_INSTRUCTION`, which requires the AI disclosure to be repeated when the greeting was cut off.

## 3. Code review result (regressions)

No regression found in the changed code. Specifically checked:
* **Defaults off.** `natural_speech=False`, the other four fields `None`. `resolve_profile(None)` and `resolve_profile({})` still return `None`. `PUT` stores `model_dump(exclude_unset=True, exclude_none=True)`, so an unset field is never persisted and an old stored profile loads unchanged.
* **Prompt.** With both options off, `render_voice_append(...)` equals the previous output exactly (asserted, and the older prompt tests in `test_voice_profile`, `test_voice_phase7b`, `test_voice_greeting`, `test_listen_first`, `test_voice_script_authority` still pass).
* **Fingerprint** (used as the greeting-audio-cache key): identical to the old formula unless a phrase option is set (test recomputes the old hash). Prompt-only options do not change it, correctly, since they do not change the audio of a fixed text.
* **`prompt_options()` keeps its 3-tuple shape**; the new options come from a separate `prompt_extras()`, so no caller or test of the old function changed.
* **`PhraseBuffer()`** with no arguments behaves exactly as before; the old phrase-buffer tests pass.
* **`admin.py`** was not modified. The `GET` response's `effective` dict does not list the new fields (they are visible in `profile`, the stored JSON). Cosmetic only.
* Limitation, not a regression: `voice_preview.synthesize(stream_words=True)` sends `len(text)+1` characters per phrase (a trailing space), and each session sends a one-character handshake, so billed characters are about 1 to 2 per job above the counts in section 9.

## 4. Configuration precedence (from the code)

| Setting | Order, highest first | Where |
|---|---|---|
| Voice id | explicit `voice_id=` argument to the session, then `profile.voice_id`, then `platform_voice_id()` = `settings.elevenlabs_voice_id` **if the env var was provided** (`"elevenlabs_voice_id" in settings.model_fields_set`), else the code constant `cRtvZrGFUW6HAyaDM8xe` | [elevenlabs_client.py:217](../apps/api/channels/voice/elevenlabs_client.py#L217), [voice_profile.py:75-80](../apps/api/channels/voice/voice_profile.py#L75) |
| Model | explicit `model_id=`, then `profile.model_id`, then `platform_model_id()` (same env-provided rule; fallback `eleven_flash_v2_5`) | same |
| stability / similarity_boost | profile value, else 0.45 / 0.80 (`LEGACY_VOICE_SETTINGS`) | [voice_profile.py:103](../apps/api/channels/voice/voice_profile.py#L103) |
| style | sent only if the model is in `STYLE_MODELS` (`eleven_multilingual_v2`): profile value, else 0.15 (`DEFAULT_STYLE`). Never sent on flash or turbo | [voice_profile.py:53,106](../apps/api/channels/voice/voice_profile.py#L53) |
| speed, use_speaker_boost | sent only if the profile sets them | `resolve_profile` |
| chunk_length_schedule | `profile.chunk_schedule`, then `VOICE_TTS_CHUNK_SCHEDULE` (validated; invalid is ignored), else not sent | [elevenlabs_client.py:282](../apps/api/channels/voice/elevenlabs_client.py#L282) |
| Phrase flush on/off | `profile.phrase_flush` if not `None`, else `VOICE_TTS_PHRASE_FLUSH` (platform) | `ElevenLabsTTSSession.__init__` |
| Phrase sizes | `profile.phrase_min_chars` / `phrase_soft_chars`, else 12 / 40 | `PhraseBuffer` |
| Text respelling | only when `profile.normalize_speech`; org `pronunciations` apply inside it | [tts_normalize.py](../apps/api/channels/voice/tts_normalize.py) |
| Prompt blocks | `speaking_style`, `natural_hinglish`, `tone`, `natural_speech`, `hindi_script`: per org. `human_style` and `script_authority`: **platform-wide** env flags. Append order: base, speaking_style, natural_hinglish, human_style, natural_speech, hindi_script, tone | [prompts.py](../apps/api/core/prompts.py), [realtime_bridge.py:534](../apps/api/channels/voice/realtime_bridge.py#L534) |

The profile is loaded **per call** from the called org (`resolve_profile(org.voice_profile)`) and passed as `ElevenLabsTTSSession(profile=state.voice)`; a call with no profile uses the platform values.

### The production `style: 0.15` mismatch (investigated; not changed)
Reproduced offline, no network: constructing a session for `eleven_multilingual_v2` with no profile gives `{stability: 0.45, similarity_boost: 0.8, style: 0.15}`; flash and turbo give no `style`.
* **Cause.** [elevenlabs_client.py:222](../apps/api/channels/voice/elevenlabs_client.py#L222) adds `style` when there is no profile and the model is in `STYLE_MODELS`, and `resolve_profile` does the same when a profile exists, so **calls always send style 0.15 on multilingual v2**. But `GET /admin/settings/voice-profile`, for an org with no profile, builds `effective` from `LEGACY_VOICE_SETTINGS` only ([admin.py:2845-2848](../apps/api/routers/admin.py#L2845)) and from `settings.elevenlabs_*` rather than `platform_*`.
* **Effect.** Only an org with no profile on multilingual v2 (that is **production's platform default**) is affected: the API omits `style` while the call sends 0.15. Where an org has a stored profile the two agree. If the env vars are unset the API also shows a different voice/model than the call uses (`CwhRBW...` vs `cRtvZ...`); with them set, as in production and pilot, they agree.
* **Impact on audio:** none; this is a display mismatch. The call behaviour is the documented intent ("DEFAULT_STYLE ... only for STYLE_MODELS").
* **Suggested fix (not applied):** make `_voice_profile_out` use `platform_voice_id()`, `platform_model_id()` and add `style` when the model is in `STYLE_MODELS`; about 6 lines plus one test.
* **Why it matters for the experiment:** N3 (multilingual v2, no profile) will send style 0.15, while N0 to N2 on turbo send none, so the model comparison also changes style. The doc labels this.

## 5. Organisation isolation

* All five new options live in the org's own `Org.voice_profile` row; no new global setting or environment flag was added, and nothing mutates `settings` at runtime in application code.
* One `ElevenLabsTTSSession` and one `PhraseBuffer` per reply; prompts are pure functions of their arguments; `ResolvedVoice` is a frozen dataclass built per call.
* Tests alternate two orgs (A with every option on, B with none) and assert no leakage of flush state, prompt blocks or fingerprints, and that two sessions never share a buffer.
* Existing risk (outside Phase 2): `scripts/voice_ab_samples.py` assigns `settings.voice_tts_phrase_flush` globally. It is a script run by hand, not application code, but do not use it inside a running server.

## 6. Hindi script handling

* **Mechanism.** `hindi_script` only appends a prompt block telling the model how to write Hindi words. **No code converts or transliterates anything**; the TTS receives exactly what the model wrote. `natural_speech` and `hindi_script` do not touch tool arguments or stored data.
* **Preservation.** Both blocks require English words, technical terms, product/brand names, people's names and numbers/codes to stay in Latin script as given, and the Devanagari block forbids translating or transliterating them. The listening scripts are built that way and a test checks that every Latin word in a Devanagari phrase also appears in its Roman twin.
* **Existing normaliser** (only if `normalize_speech`), tested on four cases:

| Input | Detected | Result |
|---|---|---|
| `Aapka bill Rs. 1500 hai.` | hinglish | `... ek hazaar paanch sau rupaye ...` |
| `आपका bill ₹1500 है।` | hi | `... एक हज़ार पाँच सौ रुपये ...` (English word kept) |
| `Your total is Rs. 1500.` | en | `... one thousand five hundred rupees.` |
| `आपका appointment कल 5 बजे confirm हो गया है, WhatsApp पर ...` | hi | only `5` became `पाँच`; `appointment`, `confirm`, `WhatsApp`, `details` untouched |
| `Sharma Interiors se Veer bol raha hoon, 2:30 PM par.` | hinglish | brand/name kept; time respelled in Roman |

* **Behaviour to know about.** A Devanagari chunk gets Devanagari number words and a Roman chunk gets Roman ones, so a model that mixes scripts inside one reply gets mixed number words. An org pronunciation entry can be Devanagari (`Veer` to `वीर` worked).
* **Not verified:** how the realtime model actually follows these blocks (needs real replies), and how ElevenLabs pronounces each script (the paid listening test). Transcripts and anything that reads reply text will contain Devanagari when that mode is on. The Devanagari example sentence in the prompt uses masculine verb forms, matching the existing "Veer" persona and other prompt examples; revisit for a female persona.
* Mid-call language detection (`language_detect.py`) works on the **caller's** words and is unaffected.

## 7. Phrase flushing review (defaults off; boundary cases run offline)

| Case | Result |
|---|---|
| Short reply "Haan ji, samajh gaya." | Held, spoken at end of reply (nothing lost) |
| "Haan ji. Aapka ghar kitne BHK ka hai?" | Joined into one phrase by default (the acknowledgement is not spoken alone, so it has no separate intonation); with `phrase_min_chars: 8` a lone "Ji haan." is released on its own (candidate N2) |
| Incomplete phrase at the end | Released by `finish()`; text preserved exactly (test) |
| `...` and `…`, decimals `3.5`, `Rs.`, initials, `?`, newline | Handled; no split inside numbers or abbreviations |
| Devanagari `।` followed by a space | Boundary |
| Quote after the full stop (`"haan ji." Phir`) | Not a boundary until later; no text lost |
| Any input | Concatenated releases plus `flush()` equal the input exactly (test) |
| **Unresolved:** no sentence punctuation for a long stretch | **Held until the end of the reply**, so first audio waits for the whole reply (the provider's own default would start at about 120 characters) |
| **Unresolved:** `।` glued to the next word (no space) | Not a boundary; same hold |

These two are real latency risks under phrase flush. They do not affect the current setup (flush is off everywhere) and are pinned by a test so a future max-hold safeguard is deliberate. Recommendation before any pilot enablement: add an opt-in maximum hold (release at the last space after about 100 characters). I did not implement it because it goes beyond this review.

## 8. Context-aware TTS and latency model

### Context-aware generation is not implemented
No `previous_text`, `next_text`, `previous_request_ids`, `context_id` or `multi-stream-input` appears anywhere in `apps/api` or `scripts` (searched), the session connects only to `/stream-input?...`, and a test records every message actually sent and asserts the keys are a subset of `{text, voice_settings, xi_api_key, generation_config, flush}`. Whether the single-context WebSocket accepts `previous_text` is **unconfirmed**; the multi-context endpoint is documented but its model support is unclear ([docs](https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/multi-context-web-socket)).

### Simulated buffering latency versus real latency
`python scripts/natural_speech_sheet.py --latency` is a **simulation of our buffering only**:
* It feeds each phrase word by word at an assumed 100, 200 or 400 characters/second and compares (a) when `PhraseBuffer` first releases text with (b) when the provider's default first chunk would fill. The 120-character figure is the documented provider default quoted in the repo's own comments, **not measured against your account**.
* It does **not** include: model time to first token (equal in both arms), network, ElevenLabs synthesis time, Plivo/playout, or how a smaller first chunk changes synthesis speed. **It is not first-audio latency**, and the 300 ms / 500 ms targets cannot be confirmed without the measured paid run.
* Now includes all 21 phrases (Roman, Devanagari, English) and an unpunctuated stress case. Results:

| Text speed | Median change | p95 change | Unpunctuated 240-char reply |
|---|---|---|---|
| 100 chars/s | -170 ms | +10 ms | **+1,190 ms** |
| 200 chars/s | -85 ms | +5 ms | **+595 ms** |
| 400 chars/s | -42 ms | +3 ms | **+298 ms** |

So for normally punctuated replies buffering is not a concern (sends earlier or within 10 ms), but the unpunctuated case can exceed the 500 ms limit at typical speeds (section 7). The phrase-size setting makes no difference on this set; it matters only for very short acknowledgements.

## 9. Proposed controlled listening experiment (not run; needs approval)

> **Superseded by Phase 3 (section 7 of [natural-speech-phase3.md](natural-speech-phase3.md)):** the experiment is now 78 jobs and 6,674 characters (about 6,830 billed) with the pilot baseline N0, the production-model candidate N3 and a maximum-hold candidate N4, and the runner exists. The 61-job figures below are kept as the Phase 2 record.

### Design review: problems found and how they are handled
1. **Fairness.** Each experiment varies one thing with the voice held fixed (platform voice, no stability/similarity/style change). Exception, labelled: **N3 multilingual v2 also sends `style: 0.15`** (section 4), and production runs that model while the pilot runs turbo.
2. **E1 tests the script strategy on turbo only**, with no flush. Production runs multilingual v2; a Devanagari result may not transfer. Add the same pairs on N3 (+1,444 chars, +$0.14 at the unverified rate) if you want that.
3. **The previously suggested `voice_compare.py --measure` command was wrong for this purpose**: it does not save audio, and it runs every candidate on every phrase (about 5,776 characters, not 4,372). **It is not the runner to use.** `scripts/voice_ab_samples.py` already saves WAVs, has `--run` and a hard `--max-chars` cap, a seed and word-sized delta feeding, but its job list is hard-coded. A small runner reading `--jobs` output must be written after approval; none exists now.
4. **Reproducibility/blinding.** `--jobs` prints the exact job list (experiment, candidate, phrase, take) in a seeded shuffle with an 8-character blind id per job, so raters cannot see candidate names; same seed gives the same list (test). Audio itself is not deterministic across takes (no seed on the WebSocket), hence the optional 3 takes.
5. **Character accuracy.** Counts are `len()` of the text (Unicode code points); the job list totals equal `--cost` totals (test: 61 jobs, 4,372 characters). Billing may differ slightly: Devanagari combining marks count as characters, `stream_words` adds a trailing space per phrase and each session sends a one-character handshake (about +1 to 2 per job, roughly +100 characters per take). Plan on about 4,500.
6. Raters hear 8 kHz mu-law, the same band as calls (as in the reference file).

### Jobs, one take (from `--cost`; matches `--jobs`)

| Experiment | Candidates | Phrases | Characters |
|---|---|---|---|
| E1 script strategy | N0 | 21 (10 pairs + 1 English reference, both scripts) | 1,444 |
| E2 phrase flush | N0, N1, N2 | 10 Roman | 2,196 (732 x 3) |
| E3 production model | N3 | the same 10 | 732 |
| **Total** | | 61 jobs | **4,372** |

Candidates: **N0** pilot config (turbo, legacy settings, no flush); **N1** phrase flush on; **N2** flush with `phrase_min_chars: 8`; **N3** multilingual v2.

### Cost (UNVERIFIED pricing, labelled)
* **Unverified API list prices** found by web search, not confirmed against your plan: flash/turbo $0.05 and multilingual v2 $0.10 per 1K characters. One take: **about $0.26** (E1 $0.072 + E2 $0.110 + E3 $0.073); three takes **about $0.77**; with the extra characters in item 5, about $0.27 and $0.80.
* **If billed in credits (unverified):** 0.5 credit per character on flash/turbo and 1 on multilingual v2 gives about 2,550 credits per take.
* The OpenAI text check for the prompt blocks (about 65k input tokens) is **not priced**: the model and price are not known to me.

### Offline instructions (nothing below makes a network request)
1. `python scripts/natural_speech_sheet.py --cost --price-per-1k 0.05 --price-v2-per-1k 0.10` (use your own prices; those are the unverified ones)
2. `python scripts/natural_speech_sheet.py --jobs --seed 7 --takes 1 > jobs.json` (exact, blind-labelled job list)
3. `python scripts/natural_speech_sheet.py --sheet > sheet.csv` (rating sheet: accent, pronunciation, intonation, natural pauses, conversational flow, human-like; `first_audio_ms` reads "not measured")
4. `python scripts/natural_speech_sheet.py --latency` (simulated buffering, section 8)
5. Tests: `python -m pytest apps/api/tests/test_natural_speech_phase2.py -q`
6. Style mismatch demo: `python -c "from apps.api.channels.voice import elevenlabs_client as e; print(e.ElevenLabsTTSSession(model_id='eleven_multilingual_v2')._voice_settings)"`

## 10. Unresolved risks and decisions

1. **Unpunctuated or glued-danda text delays first audio under phrase flush** (section 7). **Addressed in Phase 3** by the opt-in maximum hold (`phrase_max_hold_chars` / `phrase_max_hold_ms`); not yet listened to.
2. **Admin settings endpoint under-reports `style`** for no-profile orgs on multilingual v2 (section 4). **Fixed in Phase 3** (display only; runtime unchanged).
3. **There is no runner for the listening test yet** (section 9, item 3). **Built in Phase 3** (`scripts/natural_speech_run.py`, offline by default, never executed).
4. The prompt blocks are unproven with real model replies; Devanagari output could mix scripts.
5. Production org-level voice profiles were never queried, so per-org overrides in production are unknown.
6. Pricing is unverified; the latency targets are unverifiable offline.

Stopped here as instructed. No paid testing, no pilot enablement.
