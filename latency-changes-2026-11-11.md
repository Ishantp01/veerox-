# Voice latency changes — 2026-11-11 (prod)

Client reported voice replies were too slow. All changes are **prod `.env` edits only** on the EC2 box
(`ubuntu@3.139.241.196`, `~/veerox-`). No code was changed, nothing was committed.
Times below are IST (UTC+5:30).

## Starting values (before any change)

| Setting | Value in prod `.env` before |
|---|---|
| `VOICE_REPLY_WAIT_TRANSCRIPT_MS` | `2500` |
| `VOICE_ELEVENLABS_PREWARM` | not set (code default `false`) |
| `VOICE_TTS_PHRASE_FLUSH` | not set (code default `false`) |
| `VOICE_INSTANT_ACK` | not set (code default `false`) |

Other relevant prod values, **unchanged**: `VOICE_VAD_SILENCE_MS=700`, `ELEVENLABS_MODEL_ID=eleven_multilingual_v2`,
`VOICE_NATIVE_BARGE_IN=true`, `VOICE_LOCAL_VAD=true`, `VOICE_LATE_TRANSCRIPT_CUT=true`, `VOICE_SMART_TURN_TAKING=true`.

Baseline measured from prod logs (Nov 10–11): time from end of speech to first agent audio was 2.2–4.9 s per turn
(plus 0.7 s VAD silence), one outlier at 16.5 s (request queued behind a busy agent).
Breakdown: transcript wait 0.5–2.9 s, model first output 0.7–1.2 s, TTS first chunk 0.4–0.85 s.

## Changes made

### Change 1 — ~11:28 AM IST (container recreated)
Backup: `~/veerox-/.env.bak-latency-20261111` (this is the state with the **starting values above**).

| Setting | Before | After |
|---|---|---|
| `VOICE_REPLY_WAIT_TRANSCRIPT_MS` | `2500` | `1200` |
| `VOICE_ELEVENLABS_PREWARM` | unset | `true` |

### Change 2 — ~11:41 AM IST (container recreated)
Backup: `~/veerox-/.env.bak-latency2-20261111` (state after Change 1).

| Setting | Before | After |
|---|---|---|
| `VOICE_TTS_PHRASE_FLUSH` | unset | `true` |
| `VOICE_INSTANT_ACK` | unset | `true` |

## Current state of the four settings

```
VOICE_REPLY_WAIT_TRANSCRIPT_MS=1200
VOICE_ELEVENLABS_PREWARM=true
VOICE_TTS_PHRASE_FLUSH=true
VOICE_INSTANT_ACK=true
```

Phrase flush and instant ack are **untested on real calls** (code comments say they need a listening check).
Things to listen for: the "haan ji" reaction overlapping the real answer; choppy speech between sentences.

## How to revert

SSH in and restore one of the backups, then recreate the api container (`restart` alone does NOT reload `.env`).
Do it when no call is in progress.

```bash
ssh -i ~/Downloads/veerox-prod-key-us.pem ubuntu@3.139.241.196
cd ~/veerox-

# Revert ONLY Change 2 (keep the 1200 ms wait + prewarm):
cp .env.bak-latency2-20261111 .env

# Revert EVERYTHING back to the starting values:
cp .env.bak-latency-20261111 .env

docker compose -f docker-compose.yml up -d --force-recreate api
```

Revert a single setting instead (example: turn off just instant ack):

```bash
sed -i 's/^VOICE_INSTANT_ACK=.*/VOICE_INSTANT_ACK=false/' .env
docker compose -f docker-compose.yml up -d --force-recreate api
```

Verify afterwards: `grep -E '^VOICE_(REPLY_WAIT|ELEVENLABS_PREWARM|TTS_PHRASE_FLUSH|INSTANT_ACK)' .env`
and check `docker compose -f docker-compose.yml logs --since 1m api` for "Application startup complete".

If the backup files are gone, the starting values are in the table at the top.

## Findings that led to these changes

- Server is healthy (load ~0.03). Startup greeting plays in ~1.1–1.4 s.
- Test call at 11:35 AM IST (`3542a80f`): the live transcriber took ~1.05 s to deliver the caller's transcript, so the
  1200 ms cap was never hit — lowering it only helps outlier turns (2–3 s waits). First audio came 2.4 s after
  speech stopped; the reply was ~17.5 s of speech (~300 chars before first audio chunk), hence phrase flush.
- A full answer ~1 s after the caller stops is not achievable with this pipeline (0.7 s silence + transcript +
  model 0.7–1.2 s + TTS 0.4–0.85 s); instant ack is the practical way to get an audible response in ~1 s.

## Not done / still options

- `VOICE_REPLY_WAIT_TRANSCRIPT_MS=0`: reply from raw audio, saves ~1 s but risks mishearing ("travel" vs "products").
- `ELEVENLABS_MODEL_ID=eleven_flash_v2_5`: saves ~0.3–0.5 s TTS, voice sounds a bit less natural (esp. Hindi).
- Lowering `VOICE_VAD_SILENCE_MS` below 700: earlier test calls at 500 cut people off mid-sentence.
- The 16.5 s outlier (request stuck behind a busy agent) is a separate bug, not addressed.
- Prod `.env` is not in git; this file is the only record of these edits.
