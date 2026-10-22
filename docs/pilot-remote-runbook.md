# Pilot on remote infrastructure — Phase 8A runbook (PREPARED, NOTHING EXECUTED)

Plan: the FastAPI backend runs on this Windows machine; the database and Redis are NEW, separate, disposable
remote services; a secure tunnel exposes the webhooks; one isolated test org and one authorized test phone.
This replaces the Docker/PostgreSQL install options in `docs/local-pilot-runbook.md` (its flag order, probes and
cleanup advice still apply).

Every step that creates a remote resource, writes to a remote service, spends money or rings a phone is marked
**[APPROVAL]**. No secret value appears in this file.

## 1. What exists today (configuration only, nothing contacted)

| Piece | What the repo/`.env` shows | Production-like? |
|---|---|---|
| PostgreSQL | `DATABASE_URL` is a **Neon** managed Postgres (database `neondb`). Production was moved to AWS RDS on 2026-09-10 (project notes), so this is the previous production store. | **Treat as production-like**: it may hold real customer rows until you tell me otherwise. |
| Redis | `REDIS_URL` is an **Upstash** `rediss://` instance (same history). | **Treat as production-like.** |
| Tests | `TEST_DATABASE_URL` is in-memory SQLite. The full suites ran against SQLite, not the remote database. | No. |
| Tunnel | `PUBLIC_BASE_URL` is an ngrok URL. | Your dev tunnel. |
| Provider credentials in `.env` | OpenAI, ElevenLabs, Plivo, Twilio, Meta/WhatsApp, Razorpay, Brevo, `SECRET_ENCRYPTION_KEY`, `ADMIN_TOKEN` are all set. Sentry is empty. | **Real accounts.** They are the main risk (section 5). |
| Object storage | None. Recordings stay at Plivo/Twilio; only a URL is stored. | — |
| Background workers (started at API boot) | campaign dialer, WhatsApp dispatcher, follow-up dispatcher, license expiry, usage daily/monthly, AWS cost import, late-event sweep, voice retention, Plivo number registration. | They act on whatever is in the database, with whatever credentials the process has. |
| Feature flags | every Phase 1–7 voice flag defaults **off**; AI disclosure and log privacy default **on**. `PLIVO_STARTUP_REGISTRATION=false` in `.env`. | — |

## 2. PostgreSQL isolation — recommendation

**Create a new Neon project** (not a branch, not a database in the existing project) for the pilot.
- A Neon *branch* copies the parent's data: that would copy real customer rows. Do not use one.
- Roles in Neon are per project and can by default connect to every database in it, so a "restricted role" in the
  existing project does not isolate anything. A separate project has its own host, role, password and limits.
- Name the database `veerox_pilot`; use the project's default role, with a password only you hold.
- **[APPROVAL]** to create the project (free tier is enough; no card needed on the free plan, as I understand it —
  check Neon's current terms).

Identity checks that are built in (offline, then read-only):
1. `scripts\pilot_preflight.py` refuses a database whose host, port **and** name match the main `.env`, whose
   name is not exactly `PILOT_DATABASE_NAME`, whose name does not contain `pilot`/`test`, or that contains a known
   production fragment (the RDS and ElastiCache names, the EC2 address, the production domain).
2. With `--connect` it reads `current_database()` and, if an `orgs` table exists, refuses unless it holds only the
   pilot org. Select-only.
3. `python -m alembic upgrade head` uses the same settings, so it can only reach what the pilot file names. Run the
   preflight first, every time.

Synthetic data only: the API's startup seed creates the default org from `DEFAULT_ORG_ID`; add the test org's
users, scripts and leads by hand with invented names and your own test number.

Cleanup: delete the Neon project in its console. That removes the database, role and all data; there is nothing to
reconcile with production.

## 3. Redis isolation — recommendation and findings

**Use a separate Upstash database (its own endpoint and password). Sharing the existing one is BLOCKED.**
The code does not isolate itself: there is no key prefix setting, so database numbers and prefixes cannot be
relied on (and, as far as I know, Upstash offers a single logical database anyway; I did not verify that). Keys found in the code:

| Key | Scope | What a shared instance would let the pilot do |
|---|---|---|
| `veerox:kill_switch` | **global** | engage or release production's kill switch |
| `human_handoff_queue` | **global list** | put test handoffs into the production queue |
| `session:{token}` and the per-user session index | global | share the login-session space |
| `veerox:wa_msg:*` | global | suppress or replay WhatsApp message idempotency |
| `veerox:errors:{date}` | global counter | pollute the production error dashboard |
| `veerox:cache:wa_template_status` | global cache | overwrite production's cached template status |
| `veerox:phone_round_robin:*`, `veerox:transfer_round_robin:*` | per org | mostly harmless, but still shared state |
| `veerox:diag:*` | diagnostic | harmless |

Rate limiting (slowapi) uses in-memory storage, and the voice preview limiter, greeting cache, decision caches
and pending-call state are process-local, so none of those touch Redis.

**[APPROVAL]** to create the Redis database. Cleanup: delete that database in the Upstash console.

## 4. Safety changes made in Phase 8A (all inert unless `ENVIRONMENT=pilot`)

- **Pilot mode.** `ENVIRONMENT=pilot` makes every outbound call, SMS, WhatsApp message and e-mail fail unless the
  destination is in `PILOT_ALLOWED_NUMBERS` / `PILOT_ALLOWED_EMAILS`. An empty list refuses everything. The refusal
  is a normal provider error to the existing code, so nothing crashes.
- **No background workers** (dialer, dispatchers, billing jobs, Plivo re-registration) unless
  `PILOT_WORKERS_ENABLED=true`. This keeps production campaigns, follow-ups and client call queues out of reach even
  if real rows ever appear in the pilot database.
- **`VEEROX_ENV_FILE`.** The app previously always read the repo's real `.env` as a fallback for any variable a
  pilot file left out. With `VEEROX_ENV_FILE` set, only the named file is read. Without it, nothing changed.
- **`.gitignore`** now covers `.env.pilot`; the committed template is `.env.pilot.example`.
- **Booking/closing guards when the decision plan is late**, **no callbacks in the past** (5-minute slack),
  timezone and duplicate checks: see the Phase 8A report.

## 5. The real risk: credentials

With `VEEROX_ENV_FILE` the pilot never sees the main `.env`, so it only has the keys you copy into `.env.pilot`.
Leave **out** the Meta/WhatsApp, Razorpay, Brevo and Sentry keys (the example file does). Use separate OpenAI and
ElevenLabs keys with their own spending limits, and either a Plivo **sub-account** with only the test number, or
no Plivo keys at all until the first call is approved. `SECRET_ENCRYPTION_KEY` and `ADMIN_TOKEN` must be new values.

## 6. Privacy defaults for the first pilot

- AI disclosure stays on (`VOICE_GREETING_AI_DISCLOSURE=true`).
- `LOG_TRANSCRIPTS=false` and `LOG_PRIVACY_ENABLED=true` (the preflight fails if either is changed).
- **Recording off** for the test org: set its compliance policy to `{"recording_mode": "off"}` through
  `PUT /admin/settings/voice-compliance` before any call. If `always` is left, the legacy behaviour records silently.
- Conversation messages are still saved in the pilot database (that is how the agent remembers); use invented
  details only. They are destroyed with the database.
- If a recording is ever created: delete it in the Plivo console (or `scripts\probe_hangup.py plivo
  delete-recording <id> --i-approve-live-telephony`), and note the retention flags stay off.

## 7. PowerShell, in order

Run from `D:\adrsproject\veerox-`. Steps 1–4 are offline or read-only; everything else needs approval.

```powershell
# 0. One-time: create the pilot file from the template and fill it in by hand. It is git-ignored.
Copy-Item .env.pilot.example .env.pilot
notepad .env.pilot                      # fill in the NEW database URL, NEW Redis URL, tunnel URL, test phone, new keys

# 1. Make this shell load ONLY the pilot file (the real .env is then never read)
$env:VEEROX_ENV_FILE = (Resolve-Path .env.pilot).Path
Remove-Item Env:DATABASE_URL, Env:REDIS_URL -ErrorAction SilentlyContinue      # nothing may override the file

# 2. Offline preflight: reads two local files, contacts nothing
python scripts\pilot_preflight.py .env.pilot

# 3. Offline tests of the guards (SQLite, no network)
python -m pytest apps\api\tests\test_pilot_8a.py -q -p no:cacheprovider

# 4. [APPROVAL for the resources, not for this command] read-only look at the two NEW remote services
python scripts\pilot_preflight.py .env.pilot --connect        # SELECT current_database(), PING, DBSIZE only

# 5. [APPROVAL] create the schema on the pilot database; the preflight above must have passed first
python -m alembic upgrade head
python -m alembic current                                     # expect a7c9e1f3b5d2 (head)

# 6. Start the API. Startup seeds the default org on the pilot database (a write). Workers stay off.
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8002 --no-access-log
#    the log should show: pilot_background_workers_disabled

# 7. In a second window: confirm the guard rails and flags
$h = @{ 'X-Admin-Token' = '<your pilot ADMIN_TOKEN>' }
Invoke-RestMethod -Headers $h http://127.0.0.1:8002/diag/voice-config

# 8. [APPROVAL] secure tunnel; copy the https URL into PUBLIC_BASE_URL in .env.pilot, then restart the API
ngrok http 8002

# 9. Stop and clean up
Get-Process uvicorn,python -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like '*uvicorn*' } | Stop-Process
#    then: Ctrl+C the ngrok window
Remove-Item Env:VEEROX_ENV_FILE
#    then delete the Neon project and the Upstash database in their consoles, delete .env.pilot,
#    revoke or rotate any key that was pasted into a log, and remove the test number's webhook URLs at Plivo.
```

## 8. Missing infrastructure

- A **new Neon project** and a **new Upstash database** (both free tiers, as far as I know).
- Separate OpenAI and ElevenLabs keys with spending limits.
- Optionally a Plivo sub-account and one test number.
- An authorized test phone you hold.
- Nothing needs installing on this machine.

## 9. Cost considerations

No prices are asserted here; check each provider's current pricing page.
- **Neon / Upstash free tiers:** no charge expected, with usage limits; confirm before relying on them.
- **OpenAI Realtime, ElevenLabs, Plivo:** billed per use on the first call and the probes. Set a hard spending
  limit on each pilot key before use. A three-minute call costs the Plivo minutes, the Realtime audio tokens and the
  ElevenLabs characters; the system records the measured quantities with `VOICE_METERING_V2`.
- **ngrok:** your current plan; a free URL changes on restart.

## 10. Remaining blockers

1. The two remote resources above do not exist yet.
2. Whether the existing Neon/Upstash instances hold real customer data is not something I verified; I treated them as
   if they do.
3. The probes (`response.cancel`, hangup, recording delete) and the first live call are not run.
4. The compliance decisions in `docs/voice-compliance-review.md` (default recording, transcript retention) still
   need an owner before real callers are involved.

## 11. Addendum: running the pilot on the same data stores as `.env` (your instruction)

`.env.pilot` was generated from `.env`: database, Redis, tunnel URL, admin token, encryption key and the OpenAI,
ElevenLabs, Plivo and Twilio settings are copied unchanged. The Meta/WhatsApp, Razorpay, Brevo and Sentry keys are
commented out (remove the `#` only if you need one). It adds `ENVIRONMENT=pilot`, workers off, no startup Plivo
registration, transcripts not logged, AI disclosure on, `MAX_CONCURRENT_CALLS=2`, and empty allowlists.

Because those stores were production until 2026-09-10, `scripts\pilot_preflight.py` **fails** until you remove the
`#` from `PILOT_ACKNOWLEDGE_SHARED_DATA=...` in `.env.pilot`, which is your statement that the database and Redis hold
no real customer data. That line is deliberately yours to enable. It turns the "different database/Redis/tunnel"
failures into warnings; it never relaxes the production-host, worker, startup-registration, transcript, environment or
database-name checks.

What sharing means in practice:
- The database already holds 5 orgs. Pilot rows (conversations, leads, memory, usage, any callback) land in
  `DEFAULT_ORG_ID`'s org, i.e. an existing org. Prefer creating a dedicated test org and number first.
- The Redis keys are global: the kill switch, the handoff queue and sessions are shared with whatever else uses it.
- `PLIVO_PHONE_NUMBER` is the same number. In pilot mode the API now hangs up on any caller, inbound or outbound, who
  is not in `PILOT_ALLOWED_NUMBERS`, before anything is connected or recorded. While the pilot runs, only the numbers
  you list can reach the agent; with the list empty, nobody can.
- Set `PILOT_ALLOWED_NUMBERS` (your test phone, E.164) before any call.
