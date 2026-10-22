# Phase 9: production release preparation for the existing AWS deployment (22 October 2026)

Nothing here was committed, pushed or deployed, no AWS resource or provider setting was touched, and no call was placed.
"Verified" means read from this repository; "project notes" means taken from earlier sessions' notes and not checkable from the repo.

## 1. The existing deployment

| Question | Answer | Source |
|---|---|---|
| Trigger | A push to `main`, or a manual run, of `.github/workflows/deploy-aws.yml` (one deploy at a time, never cancelled) | verified |
| Mechanism | The workflow SSHes into the server and runs: `git fetch origin main`, `git reset --hard origin/main`, `docker compose -f docker-compose.yml build`, then `run --rm --no-deps api alembic upgrade head` from the new image, then `up -d`, then `docker image prune -f`. `script_stop: true`: a failed build or migration stops before any container is replaced | verified |
| Secrets it needs | `AWS_SSH_HOST`, `AWS_SSH_USER`, `AWS_SSH_KEY`, `AWS_DEPLOY_PATH` (GitHub repository secrets) | verified (names only) |
| Which GitHub repository holds them | **not known**: this checkout has two remotes, `origin` (Ishantp01/veerox-) and `nishant` (iamnishantkushwaha/veerox-); the push must go to the one whose Actions secrets are set | open |
| Compute | One host running docker compose: `api` (FastAPI, one uvicorn process, port 8002), `web` (Next.js, 3001), `caddy` (80/443, TLS, `api.workassignai.com` -> `api:8002`) | verified |
| AWS services | The repo proves SSH to one machine running docker compose and an `aws_cost_importer` worker. That it is EC2, the RDS PostgreSQL and the ElastiCache Valkey named in earlier notes are **project notes**, not provable here | partly verified |
| Runtime configuration | `env_file: .env` on the server (git-ignored). Only `ADMIN_TOKEN` is required by the code; everything else has a default | verified |
| WebSockets | Caddy `reverse_proxy` forwards upgrades; `uvicorn[standard]` provides the WebSocket stack; the voice stream is `/voice/stream` | verified |
| Process management | Docker `restart: unless-stopped`; one process runs the API, the voice streams and every background worker (dialer, dispatchers, aggregators, retention) | verified |
| Health checks | `GET /health` (liveness), `GET /ready` (database and Redis; it returns HTTP 200 with `"down"` in the body, so a monitor must read the body). No Docker or Caddy health check | verified |
| Logging / monitoring | structlog JSON on stdout (`docker logs`), phone numbers and secrets masked; optional Sentry via `SENTRY_DSN`; in-process voice latency at `GET /diag/voice-latency`; new `GET /diag/voice-active`. No log shipping or alarms are defined in the repo | verified |
| Rollback | None automated. `docker image prune -f` removes the superseded image, so a rollback means rebuilding an earlier commit | verified |

## 2. What the audit found and what was done

Real production findings in the voice path, in priority order:

| # | Finding | Action |
|---|---|---|
| 1 | `/voice/answer` and `/voice/stream` accept anyone in production (the signature and stream-token checks existed only for the pilot). Any caller could name an organisation and open a billed stream | The checks work in production now, **off by default**, rolled out `warn` -> `enforce` (section 6). Production uses expiring replay state that needs no initialisation (below) |
| 2 | The pilot's replay design is not production-suitable: a permanent ledger that fails closed when full (an outage) and needs a one-time initialisation tied to rotating the Plivo token; stream ownership that never expires after a crash or redeploy | Pilot keeps it. Everywhere else: nonces expire after 24 h (`SET NX EX`), stream ownership is a per-call key that expires after 2 h and is released by its owner. No capacity ceiling, no initialisation, cleanup after a restart by itself. Residual: a captured, already-accepted request is replayable again after 24 h |
| 3 | Nothing bounded simultaneous streams (`MAX_CONCURRENT_CALLS` only paces the campaign dialer; my earlier pilot notes that called it a concurrency limit were wrong) | `VOICE_MAX_ACTIVE_STREAMS` (default 0 = unlimited): streams over it are refused with close code 1013 before any OpenAI session; a new outbound dial fails fast (HTTP 503) |
| 4 | No server-side maximum call length (the provider's stream timeout is one hour) | `VOICE_MAX_CALL_SECONDS` (default 0 = off): hangs up through the existing provider hang-up, counted from the stream opening |
| 5 | A restart or crash closes the media stream but, because of `keepCallAlive`, may leave the phone line open and silent | `VOICE_HANGUP_ON_STREAM_END` (default off): asks the provider to end the leg (bounded to 4 s; a 404 for a call already ended is harmless). Also covers a model session that never connected |
| 6 | Tickets, stream tokens and leases cannot carry Twilio (its webhooks and streams have another signing scheme) | Not built. `enforce` is refused with an **error** in `/diag/voice-config` and the startup log while Twilio is configured |
| 7 | A pre-connected OpenAI session lived 20 s, shorter than a typical ring | Lifetime is ring timeout + 35 s **only when a ring timeout is requested**; otherwise 20 s as before |
| 8 | Start-up database reads were serial, the answer handler waited for two writes, and the organisation was read three times on three connections | Concurrent start-up with one organisation read; the answer returns first (recording decision, then usage write, after the response) |
| 9 | A "haan" while the agent was talking was logged as ignored but answered | Fixed (stale overlap list) |
| 10 | The post-deploy question "are calls live right now?" had no answer | `GET /diag/voice-active` (admin token): counts only |

Reviewed and left as they are: the single-use outbound ticket (self-expiring after 180 s, no initialisation, works in production), webhook authentication (Plivo only), call termination and cleanup (existing tests), per-organisation isolation (stream tokens bind the organisation when signatures are on).

## 3. Files in the release

105 files change in the working tree (44 modified, 61 new); the release is 103 of them. Offline result on this exact tree: the full API suite, 2,245 tests, 0 failed, 0 errors, 0 skipped. **Do not stage** `apps/web/src/components/nav.test.tsx` and `apps/web/tsconfig.tsbuildinfo` (web, not voice; the second is a build artefact). Local evidence directories `.validation-*` are now git-ignored.

- Voice and API code (37): `channels/voice/{adapter,realtime_bridge,webhook,failover,latency,config_check,turn_controller,elevenlabs_client,plivo_client,twilio_client,compliance,quality,tts_normalize,voice_profile}.py`, new `capacity.py` and `signature.py`, `preview_phrases.json`; `core/{privacy,prompts,tools}.py` and `core/conversation/{dialogue,memory,reply,silence,voice_decision}.py`; `config.py`, `logging.py`, `main.py`, `routers/{admin,diag}.py`; the pilot guard module and checks (`pilot.py`, `pilot_checks.py`, `pilot_setup.py`) and the guards in `channels/whatsapp/*` and `channels/email/brevo_client.py` (all inert outside `ENVIRONMENT=pilot`).
- Tests (29), scripts (13, copied into the image, never run by it), docs (18), and four repo files: `.gitignore`, `pyproject.toml` (a test-only dependency), `uv.lock` (checked consistent with `uv lock --check`), `.env.pilot.example`.
- No migration, no Dockerfile, compose, Caddy or workflow change.

Behaviour that changes on deploy **even with every new setting at its default**: the overlap fix (nods are no longer answered), the callback tool (rejects a callback time more than five minutes in the past, reports a duplicate callback once, and only says a team member was notified when one was), the answer handler's ordering, the concurrent start-up, extra log lines (`voice_turn_trace`, `voice_startup_timeline`), and uvicorn's own log lines now pass through the same JSON/masking formatter. None of these needs configuration.

## 4. Environment variables

All optional. A server that sets none of them behaves as it does today (apart from the list above).

| Variable | Default | Purpose |
|---|---|---|
| `PLIVO_SIGNATURE_MODE` | `auto` (= off outside the pilot) | `off` / `warn` / `enforce` |
| `PLIVO_REPLAY_STATE` | `auto` (expiring outside the pilot) | `strict` / `expiring` |
| `PLIVO_NONCE_EXPIRING_TTL_SECONDS` | 86400 | how long a nonce is remembered |
| `PLIVO_STREAM_LEASE_SECONDS` | 7200 | longest a stream lease can outlive a dead owner |
| `PLIVO_STREAM_TOKEN_TTL_SECONDS` | 300 | stream URL token lifetime |
| `PLIVO_CALL_TICKET_TTL_SECONDS` | 180 | single-use outbound answer ticket |
| `VOICE_MAX_ACTIVE_STREAMS` | 0 | per-process cap on live calls |
| `VOICE_MAX_CALL_SECONDS` | 0 | hard call length |
| `VOICE_HANGUP_ON_STREAM_END` | false | end the provider leg when the stream ends |
| `VOICE_OUTBOUND_TIME_LIMIT_SECONDS`, `VOICE_OUTBOUND_RING_TIMEOUT_SECONDS` | 0 | provider-enforced length and ringing of outbound calls (also lengthens the pre-connected session) |
| `VOICE_GREETING_AUDIO_CACHE` | false | play the org greeting from audio synthesised at dial time |
| `PILOT_*`, `VEEROX_ENV_FILE`, `PLIVO_NONCE_CAPACITY`, `PLIVO_STREAM_CAPACITY` | inert | pilot only |
| `PLIVO_NONCE_TTL_SECONDS`, `PLIVO_STREAM_TOKEN_MAX_USES` | unused | kept so an old `.env` still loads |

Changing the server's `.env` means recreating the `api` container (`docker compose up -d`): the same short outage as a deploy.

## 5. Database, Redis, AWS

- **Migrations:** none. One Alembic head (`a7c9e1f3b5d2`), unchanged; the deploy's `alembic upgrade head` is a no-op.
- **Redis:** nothing to initialise in production. With the defaults the new code makes no extra Redis call on a call. With signatures on it adds a few short-lived keys per call (nonce, ticket, lease), all expiring.
- **Database connections:** a call start now opens two connections at once (conversation and organisation read) instead of one at a time (before this release, three reads were serial; an interim version of this branch used four). Check the database's `max_connections` against your busiest dial burst.
- **AWS changes required:** none. Optional, not made: `stop_grace_period: 30s` on the `api` service so teardown (usage writes, leg hang-ups) is not cut at Docker's default 10 s; a `healthcheck` on `/health`; a periodic `docker builder prune` (the workflow prunes only dangling images, and the build cache fills the disk over time, per earlier notes); alarms on 5xx and on `voice_stream_error`.

## 6. Turning the protections on, after the release is live and healthy

Each step is an edit of the server's `.env` followed by `docker compose -f docker-compose.yml up -d api` (a short outage; do it when `/diag/voice-active` shows no calls).
1. `VOICE_MAX_ACTIVE_STREAMS` (a number below what the host can carry: size it with a load test, the repo's `longrunning/operations/load.md` is the reference), `VOICE_MAX_CALL_SECONDS` (your longest legitimate call plus margin), `VOICE_HANGUP_ON_STREAM_END=true`, `VOICE_OUTBOUND_TIME_LIMIT_SECONDS` and `VOICE_OUTBOUND_RING_TIMEOUT_SECONDS`.
2. `PLIVO_SIGNATURE_MODE=warn`. Make real inbound and outbound calls and watch the log for `voice_webhook_would_reject` and `voice_stream_would_reject`. Their `reason` and the logged `plivo_*` status words say why. Expected causes to clear first: a `PUBLIC_BASE_URL` that is not the exact https root the provider calls (`host_matches_public_base: false`), organisations with their own Plivo account (their inbound calls carry no organisation in the URL), and Twilio traffic.
3. `PLIVO_SIGNATURE_MODE=enforce` only when a full week of real traffic shows no unexplained would-reject lines **and Twilio is not configured**. A Plivo-only deployment is required; `/diag/voice-config` reports the conflict as an error.
Rollback of any step: put the previous value back and recreate the container.

## 7. Impact on live calls, downtime, rollback

- A deploy recreates the `api` container: Docker sends SIGTERM, waits 10 s, then kills it. **Every call in progress ends**, the API is unavailable for roughly the stop time plus start-up (the build itself happens beforehand while the old container serves), Caddy answers 502 meanwhile, and a Plivo webhook arriving in that gap fails. The deployment is shared with real users if any call is in flight: **check `GET /diag/voice-active` first and deploy when it is 0** (the existing rollout notes already say to deploy outside calling windows). With `VOICE_HANGUP_ON_STREAM_END` on, the dropped calls are also ended at the provider instead of left open.
- The web container is rebuilt too (the build is in the same compose file), so the dashboard blinks as well.
- No schema change, so the new image runs against the existing database, and the previous image does too: rollback is code only.
- **Rollback:** preferred, `git revert <release commit>` and push (the same pipeline redeploys the old code in about the same time); fast manual path on the server: `cd $AWS_DEPLOY_PATH && git reset --hard <previous good sha> && docker compose -f docker-compose.yml build && docker compose -f docker-compose.yml up -d`. The previous image is not kept (it is pruned), so both take a rebuild. Settings added to `.env` can stay: older code ignores unknown keys.
- Backward compatibility: yes for configuration, data and the API; the only visible differences are the default-on behaviours listed in section 3.

## 8. Not verified

Everything is offline with fake sockets and providers. Not proven: any of this on a real call (the Phase 8L changes in particular have never run on a call), Plivo's signing of production traffic, uvicorn's behaviour on SIGTERM with live WebSockets, the database's connection headroom, and the "assume 8L passes" premise of this phase. The first calls after the deploy are the test.
