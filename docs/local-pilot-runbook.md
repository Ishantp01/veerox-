# Controlled local pilot — Windows runbook (PREPARED, NOT EXECUTED)

Nothing in this file has been run. Every step that calls a paid service or rings a phone is marked **[APPROVAL]**
and must not be done without your explicit go-ahead. No secret values appear here; set them yourself in a
local, git-ignored environment file or in the shell.

## 0. What this machine has today

| Needed | Found on this machine |
|---|---|
| Python 3.13, `uv` | yes |
| ngrok (WinGet install) | yes |
| Docker | **no** |
| PostgreSQL / Redis | **no** (the current `.env` points at remote services, see section 2) |

So you need a disposable database and Redis before any pilot. Pick ONE:

```powershell
# Option A - Docker Desktop (needs a reboot/WSL2 the first time)
winget install -e --id Docker.DockerDesktop
docker compose -f compose.yml up -d db redis        # postgres:16 + redis:7 from compose.yml, ports 5432/6379

# Option B - native, no Docker
winget install -e --id PostgreSQL.PostgreSQL.16     # then create an empty database named veerox_pilot
winget install -e --id Redis.Redis                  # if unavailable, use Memurai Developer (free) instead
```

## 1. Isolated environment file

Create `D:\adrsproject\veerox-\.env.pilot` (never commit it; the repo `.gitignore` should already cover `.env*` —
check with `git check-ignore .env.pilot`). Names only:

```
DATABASE_URL            postgresql+asyncpg://<user>:<password>@localhost:5432/veerox_pilot
REDIS_URL               redis://localhost:6379/0
ADMIN_TOKEN             <a new random value, not the production one>
DEFAULT_ORG_ID          00000000-0000-0000-0000-000000000001
PUBLIC_BASE_URL         https://<your-tunnel-host>            # no trailing path
CORS_ALLOWED_ORIGINS    http://localhost:3001,http://127.0.0.1:3001
OPENAI_API_KEY          <a key with a spending limit you set>
OPENAI_REALTIME_MODEL   gpt-realtime-2.1
VOICE_TTS_PROVIDER      elevenlabs
ELEVENLABS_API_KEY      <key>
ELEVENLABS_MODEL_ID     eleven_flash_v2_5
ELEVENLABS_VOICE_ID     <voice id>
PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN / PLIVO_PHONE_NUMBER   <a test number/account, not the customers'>
LOG_PRIVACY_ENABLED     true
LOG_TRANSCRIPTS         false          # keep false for the pilot; turn on only to debug one call
VOICE_LATENCY_TRACKING  true
```

Do not copy production values. Do not point this file at the Neon or RDS databases.

Phase 7 flags, **all off by default**; enable one at a time in the order below, restarting the API each time:

```
VOICE_SMART_TURN_TAKING=true        # 7A: thinking pauses, unfinished sentences, patient silence, counters
VOICE_METERING_V2=true
VOICE_GREETING_AI_DISCLOSURE=true
VOICE_SAFE_CANCEL=true              # ONLY after the cancel probe in section 6 passes
VOICE_TRIM_INTERRUPTED_CONTEXT=true # only after safe cancel is proven
VOICE_END_CALL_ENABLED=true         # only after the hangup probe in section 6 passes
```

Per-organization options (set on the isolated test org only, via `PUT /admin/settings/voice-profile` and the
Scenario Builder): `normalize_speech`, `natural_hinglish`, `tone`, `chunk_schedule`, `speaking_style`, and
`enhanced_dialogue`. Existing organizations are untouched because none of these are global.

## 2. Start the backend (PowerShell, from the repo root)

```powershell
cd D:\adrsproject\veerox-
uv sync                                              # or: python -m venv .venv ; .\.venv\Scripts\pip install -e .
Get-Content .env.pilot | ForEach-Object { if ($_ -match '^\s*([^#=\s]+)\s*=\s*(.*)$') { Set-Item "env:$($Matches[1])" $Matches[2] } }
python -m alembic upgrade head                       # against the DISPOSABLE database only
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8002 --no-access-log
```

Check `$env:DATABASE_URL` shows `localhost` before running Alembic. `scripts\validate_migrations.py` refuses
production hosts and can be run first against the empty database:

```powershell
python scripts\validate_migrations.py $env:DATABASE_URL --yes-this-is-not-production
```

## 3. Secure tunnel

```powershell
ngrok http 8002          # HTTPS and WSS both work through it; copy the https URL into PUBLIC_BASE_URL, restart the API
```

Free ngrok URLs change on restart; if it changes, update `PUBLIC_BASE_URL` and restart the API, then update the
Plivo application URLs (section 4).

## 4. Isolated test organization and Plivo webhook

1. Create a new org (not a customer's) with its own admin, a dialable test number, and its own Plivo credentials
   in the org settings. Seed it through the app or `scripts\seed.py` pointed at the pilot database.
2. In the Plivo console, point the test number's application **Answer URL** at `<PUBLIC_BASE_URL>/voice/answer`
   (method POST). The audio stream is `<PUBLIC_BASE_URL>` as `wss://…/voice/stream`; recording callbacks go to
   `/voice/recording-callback`.
3. Verify without a call: `Invoke-RestMethod <PUBLIC_BASE_URL>/health` returns ok through the tunnel, and
   `Invoke-RestMethod -Headers @{ 'X-Admin-Token' = $env:ADMIN_TOKEN } http://127.0.0.1:8002/diag/voice-config`
   lists the flags and any `error` issue (fix every error before continuing).

## 5. Authorized test phone

Use a phone you own, with its number saved as the only allowed destination for the pilot org. Tell anyone who can
hear the call that it is a recorded test and that the agent is an AI. Do not call customers.

## 6. Probes and tests that spend money or ring phones — **[APPROVAL] each**

Estimated spend is at the bottom; none of these has been run.

```powershell
# a. OpenAI Realtime: does response.cancel keep the session open?  (a few cents)
python scripts\probe_realtime_cancel.py --i-approve-a-paid-openai-call

# b. Plivo: does the hangup API end a call, and does recording deletion work? (needs one real test call first)
python scripts\probe_hangup.py plivo hangup <call_uuid> --i-approve-live-telephony
python scripts\probe_hangup.py plivo delete-recording <recording_id> --i-approve-live-telephony

# c. ElevenLabs: voice comparison, first-audio latency (characters billed)
python scripts\voice_compare.py --measure --price-per-1k <your price>

# d. Rating sheet for listeners
python scripts\voice_quality_rate.py sheet scripts\voice_comparison_phrases.txt --seed 7 --out sheet.json
```

Then the scenario calls in `scripts\voice_validation_scenarios.json` (S01–S17c), one at a time:

```powershell
python scripts\voice_postcall_verify.py S04 --logs api.log --call-uuid <uuid> --conversation conv.json
Get-Content api.log | python scripts\voice_latency_report.py          # needs hundreds of turns to mean anything
```

## 7. Recording and transcript privacy checks (offline, free)

```powershell
Select-String -Path api.log -Pattern '\+?\d{10,15}'                    # expect no unmasked phone numbers
Select-String -Path api.log -Pattern 'voice_user_transcript' | Select-Object -First 3   # expect "<n chars not logged>"
```

Also confirm in the org settings that the recording mode is what you intend (`always` records silently by
default), and read `docs\voice-compliance-review.md` before using real callers.

## 8. Stopping and cleaning up

```powershell
Stop-Process -Name uvicorn -ErrorAction SilentlyContinue ; ngrok.exe is stopped with Ctrl+C
docker compose -f compose.yml down -v          # Docker option: deletes the disposable database and Redis data
```

Remove `.env.pilot`, delete test recordings in the Plivo console, and rotate any key that was pasted into a log.

## Approvals and rough cost drivers (no prices are asserted)

| Step | Cost driver | Needs your approval |
|---|---|---|
| Cancel probe | one short Realtime session | yes |
| Each test call (about 3 minutes) | Plivo minutes + Realtime audio tokens + ElevenLabs characters | yes, per call batch |
| Voice comparison | ElevenLabs characters for ~12 phrases x variants | yes |
| Hangup / recording-delete probes | Plivo API requests (and the call it needs) | yes |
| Installing Docker/PostgreSQL/Redis | none, but changes the machine | yes |

Look up current prices on each provider's pricing page and multiply by the measured quantities in the
`usage_events` table (`VOICE_METERING_V2`); the system records measured quantities only, not money.
