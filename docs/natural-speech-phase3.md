# Natural Speech Quality, Phase 3: listening-test preparation (offline)

Status: implemented and verified offline. **Nothing is enabled, no paid test was run.** Read with [natural-speech-phase2.md](natural-speech-phase2.md) (design, configuration precedence, risks) and [natural-human-speech-quality-analysis.md](natural-human-speech-quality-analysis.md) (Phase 1).

## 0. Confirmations

| Item | Status |
|---|---|
| OpenAI, ElevenLabs or Plivo requests | **None.** A test fixture fails any test that tries to open a real ElevenLabs connection; the runner tests use a fake socket |
| Paid audio generated, calls placed | **None** |
| Pilot or production configuration changed | **None.** No `.env*` file, database row or org profile was written. The only reads were in earlier rounds (production voice id/model grep, one pilot `SELECT`) |
| Any new option enabled | **None.** All new profile fields are unset by default; the pilot org's profile is still empty |
| Deployed, committed or pushed | **No.** Everything is uncommitted in the working tree |
| Credentials exposed | **No** |
| Local environment | Only `fakeredis` was installed (previous round). Nothing new this round |

## 1. Files changed this phase

| File | Change |
|---|---|
| [apps/api/channels/voice/elevenlabs_client.py](../apps/api/channels/voice/elevenlabs_client.py) | `PhraseBuffer` maximum hold (`max_hold_chars`, `max_hold_seconds`, injectable clock, `poll()`, `deadline()`); session timer (`_hold_watch`) that flushes stalled text; session builds its no-profile settings from the shared `default_voice_settings()` (same values as before) |
| [apps/api/channels/voice/voice_profile.py](../apps/api/channels/voice/voice_profile.py) | New optional fields `phrase_max_hold_chars` (40 to 400) and `phrase_max_hold_ms` (200 to 5000); contradiction check with `phrase_flush: false`; fingerprint includes them only when set; `default_voice_settings(model_id)` as the single source of the no-profile settings |
| [apps/api/routers/admin.py](../apps/api/routers/admin.py) | `_voice_profile_out` (the settings endpoint) now reports the platform voice/model a call really resolves and the settings it really sends; the new fields appear in `effective` |
| [scripts/natural_speech_run.py](../scripts/natural_speech_run.py) (new) | The blind audio runner (offline by default) |
| [scripts/natural_speech_sheet.py](../scripts/natural_speech_sheet.py) | Buffering simulation now models the maximum hold; experiments now include N4 |
| [scripts/natural_speech_analyze.py](../scripts/natural_speech_analyze.py) (new) | Offline analysis: ratings per candidate and measured latency paired with the baseline |
| [docs/natural-speech-eval/phrases.json](natural-speech-eval/phrases.json), [candidates.json](natural-speech-eval/candidates.json) | Added the run-on stress pair and candidate N4; N0 is described as the pilot, N3 as the production model |
| [apps/api/tests/test_natural_speech_phase3.py](../apps/api/tests/test_natural_speech_phase3.py) (new) | 65 tests |
| [docs/natural-speech-phase3.md](natural-speech-phase3.md) (new), [docs/natural-speech-phase2.md](natural-speech-phase2.md) | This report; the Phase 2 report's open items are marked resolved |

Unchanged: stability, similarity, style, speed, voice and model values; every existing flag; `realtime_bridge.py`; the prompt text.

## 2. Task 1: maximum-hold phrase flushing

**Options** (per organisation, in `Org.voice_profile`; both unset by default):
* `phrase_max_hold_chars`: release waiting text once it is this many characters long.
* `phrase_max_hold_ms`: release waiting text once it has waited this long.
* Either one needs phrase flushing (the org's `phrase_flush`, or the platform flag). If the org sets `phrase_flush: false` together with a hold, the save is rejected (422); with flushing simply off, the hold is ignored.

**Behaviour**
1. A normal phrase boundary always wins (sentence end after at least 12 characters, comma after 40, abbreviations and decimals respected). The hold only acts when there is no boundary.
2. When the hold is reached the cut is made at the latest comma/semicolon, otherwise at the latest space. **Never inside a word** (the unfinished last word stays in the buffer), and **never a piece shorter than the minimum** (default 12 characters, `phrase_min_chars` to change). A short acknowledgement ("Haan ji.") is therefore never sent alone by the hold; it waits and is joined to what follows or spoken at the end of the reply.
3. Punctuation-free long replies: released at the character cap or the time cap, whichever comes first.
4. Slow generation: the time cap is checked when each new piece arrives, **and a timer** releases waiting text even if the model sends nothing more. The timer exists only when `phrase_max_hold_ms` is set, is cancelled when the turn finishes or the socket closes, and shares a lock with normal sends so text is never reordered or sent twice.
5. Devanagari: with either hold option set, a danda glued to the next word (`...जाएगा।आगे`) is treated as a sentence end (a space is inserted so the words do not run together). A danda followed by a space already worked. A danda early in the text that would make a tiny fragment (`हाँ।आगे`) does not cut. Without a hold option the buffer behaves exactly as before.
6. Each cut restarts the wait clock for what remains.

**Test coverage** (all deterministic: an injected fake clock for the buffer; for the timer a stall of three times the hold with real sleeps of at most 0.6 s):
* default buffer unchanged without a hold (unpunctuated text still waits; glued danda does not cut);
* character cap on 300 punctuation-free characters: first release at about the cap, ends at a space, no text lost or repeated;
* comma preferred over a bare space; time cap triggered by the next word; `poll()` releases stalled text and then reports nothing waiting;
* short acknowledgement stays held after 5 s, joins a following question, is spoken at the end; a cut that would leave a fragment under 12 characters is refused;
* glued, spaced and too-early danda; decimals (`3.5`) and abbreviations (`Rs.`) unaffected;
* 8 randomised token-sized streams (splits inside words, random delays, random polls): nothing lost, repeated or cut inside a word, no piece under 12 characters;
* session with a fake socket: stalled stream flushed by the timer, acknowledgement not flushed by the timer, no timer or behaviour change without a hold, hold ignored without flushing, out-of-range values rejected, fingerprint changes only when set, no sharing of buffers or locks between organisations.

**What this does not show.** The offline simulation (`natural_speech_sheet.py --latency`) models **our buffer only**. With a 100-character / 500 ms hold an unpunctuated 240-character reply is released at about 0.5 s of text instead of waiting for the whole reply (the simulated wait relative to the provider's default is -640, -90 and -45 ms at 100, 200 and 400 characters/s). That is a statement about when text reaches ElevenLabs, **not a first-audio latency result**: provider synthesis time, network, and whether ElevenLabs speaks a mid-sentence `flush` as smoothly as a full sentence are all unmeasured. A forced cut in the middle of a sentence may sound flatter than a cut at a boundary; that is a listening question.

## 3. Task 2: blind audio runner (`scripts/natural_speech_run.py`)

**Offline by default.** With no opt-in flags it writes files only; it makes zero provider requests and generates no audio (a test asserts no connection is even attempted).

```
# Offline: writes the sheet, the key and a ledger of "planned" jobs
python scripts/natural_speech_run.py --out runs/r1 --seed 7 --takes 1

# PAID (do not run without approval). Needs ALL of: --run, --allow-provider-requests, --max-chars,
# a configured ELEVENLABS_API_KEY, and VOICE_TTS_PHRASE_FLUSH off.
python scripts/natural_speech_run.py --out runs/r1 --seed 7 --takes 1 \
    --run --allow-provider-requests --max-chars 4600
python scripts/natural_speech_run.py --out runs/r1 --seed 7 --run --allow-provider-requests --max-chars 4600 --resume
```

Use `VEEROX_ENV_FILE=.env.pilot` if the baseline should be the pilot's voice and model (the key records exactly what each clip used). Use a fresh `--out` per seed.

**What it writes**
* `listening/sheet.csv`: the rating sheet. Columns: anonymous `clip_id`, `set_id` (same sentence), anonymous `label`, category, script, the text, then blank **indian_accent, pronunciation, rhythm, intonation, pauses, naturalness** and notes. Nothing in it names a model, voice, flag or experiment (a test checks).
* `listening/README.txt`: the 1 to 5 scale and rating instructions.
* `listening/clips/<clip_id>.wav` (paid run only): 8 kHz PCM, the phone band.
* `KEY/key.json`: clip id to experiment, candidate, anonymous label, exact profile and the **effective** voice id, model and voice settings (so the multilingual-v2 `style 0.15` is on record). Kept in a separate folder (`--key-dir` to put it elsewhere); do not give it to raters.
* `KEY/results.jsonl`: one line per job with planned characters and, after a run, characters actually sent, time to first audio, audio bytes, status and a short error code.

**Anonymous labels and order.** Candidate labels (such as `K4`) are drawn from the seed, jobs are shuffled with the seed, and the sheet is grouped by sentence with a shuffled order inside each group. The same seed gives the identical sheet; another seed gives different labels (tests).

**Safety and failure handling**
* A paid run is refused (exit 2, nothing sent) unless `--run` **and** `--allow-provider-requests` are both given, `--max-chars` is set and not lower than the estimate, the baseline N0 resolves to the pilot model `eleven_turbo_v2_5` (planned characters plus 2 per job, 6,830 for one take), an API key exists, and the platform phrase-flush flag is off (otherwise the baseline would not be the baseline).
* A failed job (connection error, provider error message, timeout, no audio) is written to the ledger as `failed` with a code, never dropped; the summary lists them and the exit status is 1. `--resume` skips jobs already `ok` and retries only the rest (tested: 59 ok / 2 failed, then 2 retried).
* The ledger is appended and flushed after every job, so a crash loses nothing.
* The runner reads configuration only; it changes no setting, organisation or environment value.

**Tested against a fake socket:** all 78 jobs run, one clip per job named only by its blind id, characters sent equal characters planned per job and in total (6,674).

## 4. Task 3: settings display mismatch

* **Cause (as reported earlier):** for an org with no profile, the endpoint listed only stability and similarity, and read `settings.elevenlabs_*` instead of the platform values a call resolves. Calls on multilingual v2 send `style: 0.15`.
* **Fix:** one function, `default_voice_settings(model_id)`, now builds the no-profile settings and is used by **both** the request (`ElevenLabsTTSSession`) and the endpoint. The endpoint also uses `platform_voice_id()` / `platform_model_id()`. **Runtime behaviour is unchanged:** a test pins the golden payloads (flash and turbo: stability 0.45, similarity 0.8; multilingual v2: those plus `style` 0.15), and the pre-existing client tests pass.
* **Production's effective `style: 0.15` is preserved** (golden test) and is now shown by the endpoint.
* **Tests comparing display with the real request:** 24 combinations (8 profile shapes, including none, empty, per-model, speed, style, speaker boost, custom voice, Phase 2/3 options) x (3 platform models: flash, turbo, multilingual v2). For each, the voice id, model id and `voice_settings` in `effective` equal what a fake socket receives in the real handshake and URL. No production organisation profile was queried.

## 5. Task 4: verification

Command: `python -m pytest apps -q -p no:cacheprovider -rs`, run once on the final code, after the last code edit.

| Passed | Failed | Skipped | Errors | Warnings | Duration |
|---|---|---|---|---|---|
| **2862** | **0** | **0** | **0** | 8 | **727.29 s (12 min 07 s)** |

2,862 is the previous 2,802 plus the 60 new tests. The 8 warnings are the same FastAPI "Duplicate Operation ID" notices in `channels/voice/webhook.py` as before, a file not touched in any phase. Not run: nothing needing a real provider, database server or phone (the suite uses fakes).

**Backward compatibility.** Without the new fields: `PhraseBuffer()` and the session behave byte for byte as before (old tests unchanged and passing; a test pins the exact messages); the profile fingerprint (greeting-cache key) is unchanged; `prompt_options()` and the prompt are unchanged; stored profiles load as before and `exclude_none` means an unset new field is never persisted. `elevenlabs_client` still re-exports `STYLE_MODELS`, `DEFAULT_STYLE` and `LEGACY_VOICE_SETTINGS` (a script uses them). The only visible change for existing orgs is the settings endpoint now also lists the new fields and reports the true style/platform values.

**Organisation isolation.** The new settings live only in each org's own `voice_profile` row and reach the code through the per-call `ResolvedVoice`. A session owns its own `PhraseBuffer`, hold timer and send lock; nothing is module-global; the runner mutates no setting. Tests alternate two orgs and assert no shared buffer, lock, flag or fingerprint.

**Changed files inspected.** All diffs were read line by line. Findings: no regression; `scripts/voice_consistency_samples.py` depends on names `elevenlabs_client` used to import, so they are still re-exported; the hold timer can be cancelled while it is mid-send (when the turn finishes); I judged that low risk but have not tested it against a real socket, and a stricter version would take the send lock before cancelling. No linter (ruff/pyflakes) is installed in this environment, so only `py_compile` and the test suite were run on the new code.

## 6. Remaining limitations

1. **No real latency evidence.** The simulation is our buffering only. The 300/500 ms targets cannot be confirmed offline.
2. **Mid-sentence flushes are unlistened.** Whether a forced cut sounds acceptable, and whether ElevenLabs treats `flush: true` on a partial sentence well, needs the listening test. The hold is now candidate N4 in the experiment (section 7).
3. **The timer only runs while text is waiting.** If the waiting text is shorter than the minimum, nothing is sent until more text arrives or the turn ends (deliberate: no tiny fragments).
4. A long run with no space at all (a very long URL, for example) cannot be cut and is held until the end or the next space.
5. A forced release is a plain comma/space cut; it does not know about numbers or names (the optional text normaliser runs before it and already protects numbers by buffering).
6. The runner has never made a real request. Its streaming session is the same one calls use and is tested against a fake socket, but the first paid run could still hit provider behaviour the fake does not show (rate limits, closed sockets); failures would be recorded and resumable.
7. All pricing below is unverified.

## 7. The paid experiment (not run): updated for your decisions

**Your decisions applied (all offline):** baseline = the pilot (`eleven_turbo_v2_5`); the maximum-hold candidate is included as **N4**; the production model `eleven_multilingual_v2` is **N3**; every candidate is compared on pronunciation, Indian accent, naturalness, rhythm, intonation, pauses and latency.

| Candidate | Configuration (voice and stability/similarity held fixed) |
|---|---|
| **N0** | Baseline = pilot: platform voice, `eleven_turbo_v2_5`, 0.45 / 0.80, no phrase flush |
| **N1** | N0 + phrase flush |
| **N2** | N1 + 8-character minimum phrase |
| **N3** | Production model `eleven_multilingual_v2`, no phrase flush. It also sends `style 0.15`, so this row compares the model and that style together |
| **N4** | N1 + maximum hold (`phrase_max_hold_chars` 100, `phrase_max_hold_ms` 500) |

A new **run-on stress sentence** (Roman and Devanagari, about 210 characters, no punctuation at all) was added to the scripts, because the hold never acts on well-punctuated sentences and would otherwise show nothing.

**Latency** is not rated by ear. The runner measures the time from the first streamed word to the first audio chunk for every clip, and `scripts/natural_speech_analyze.py` reports each candidate's median and p95, plus the **paired** difference to N0 on the same sentence and take, with your targets (median +300 ms, p95 +500 ms) printed as pass/miss. It is the runner's measurement over the real network and includes the pacing of the streamed words; with 11 pairs per candidate the p95 is indicative only.

**Rating columns** (1 to 5): `indian_accent`, `pronunciation`, `rhythm`, `intonation`, `pauses`, `naturalness`.

### Jobs and characters (one take; the plan, the runner's ledger and the cost script all agree, tested)

| Experiment | Candidates | Phrases | Characters |
|---|---|---|---|
| E1 script strategy (Roman vs Devanagari, 11 pairs + English reference) | N0 | 23 | 1,869 |
| E2 phrase flush and maximum hold (Roman, includes the run-on sentence) | N0, N1, N2, N4 | 11 each | 3,844 (961 x 4) |
| E3 production model | N3 | the same 11 | 961 |
| **Total** | 5 candidates | **78 jobs** | **6,674** |

(Previously 61 jobs and 4,372 characters; the change is the run-on pair, N4 and the larger E2.)

| Characters | One take | Three takes |
|---|---|---|
| Planned (text) | 6,674 | 20,022 |
| Estimated billed (plus 2 per job: a trailing space and the handshake; Devanagari counting by ElevenLabs unverified) | **6,830** | 20,490 |
| Recommended `--max-chars` | 6,900 | 20,600 |

### Estimated cost, UNVERIFIED
Prices are public API list prices found by search ($0.05 per 1K characters flash/turbo, $0.10 multilingual v2), **not confirmed against your plan or the current pricing page, and you have not confirmed your plan's price.**

| | One take | Three takes |
|---|---|---|
| On planned characters | $0.382 | $1.145 |
| On estimated billed characters (flash/turbo 5,847 + multilingual v2 983) | **$0.391** | **$1.172** |
| In credits at 0.5 per flash/turbo and 1 per multilingual v2 character (also unverified) | about 3,907 | about 11,720 |

The OpenAI text step for judging the prompt blocks is still unpriced (no model chosen).

### Steps (nothing below has been run)
1. Offline preview (safe, repeatable): `VEEROX_ENV_FILE=.env.pilot python scripts/natural_speech_run.py --out runs/r1 --seed 7` (confirmed here: N0 resolves to `eleven_turbo_v2_5`, 78 jobs, 6,674 characters, no request).
2. **Paid run, needs your explicit approval and a confirmed price:** `VEEROX_ENV_FILE=.env.pilot python scripts/natural_speech_run.py --out runs/r1 --seed 7 --run --allow-provider-requests --max-chars 6900`. It refuses unless N0 resolves to the pilot model (`--baseline-model` to change it), phrase flush is off platform-wide, a key is set and the cap covers the estimate. `--resume` retries only failed jobs.
3. Raters fill `listening/sheet.csv` (copy it to `sheet_filled.csv`), without `KEY/`.
4. `python scripts/natural_speech_analyze.py --key runs/r1/KEY/key.json --ledger runs/r1/KEY/results.jsonl --sheet runs/r1/listening/sheet_filled.csv`.

## 8. Decisions still needed from you

1. **Confirm your plan's real price** per character or credit (the figures above are unverified) and **explicitly approve the paid run**. Your updated decisions settled the design but did not approve spending, so nothing was run.
2. Which raters will listen, and whether one of them is a native Hindi speaker (the Indian-accent column depends on it).
3. Whether `.env.pilot`'s ElevenLabs key may be used for the run (it is shared with the main `.env` by your earlier decision, so the run would bill the same account).

Stopped here: no flag enabled, no listening test executed.
