# Voice rollout plan (Phases 1–5 features)

Nothing is deployed or committed. All new flags default **off**; existing customers see no change until an admin
turns something on. Phase 1–5 code is uncommitted in the working tree.

## 0. Release hygiene

- Review and commit the working tree on a branch (needs your approval), run the full API suite in CI.
- Apply migrations **before** the new code starts: `e2a4c6d8f0b1` (voice_greeting), `f3b5d7e9a1c2` (voice_profile),
  `a7c9e1f3b5d2` (voice_brand, voice_compliance). All are `ADD COLUMN … NULL` with no default and no backfill, so they
  are fast, do not lock for long, and old code ignores them. Offline SQL checked; downgrade drops the columns.
  Not yet run against PostgreSQL (see `scripts/validate_migrations.py`).
- Production runs one API container (docker-compose on one EC2 host). Several things are process-local (pending
  pre-calls, greeting/fallback audio caches, latency stats, decision caches, preview rate-limiter). **Do not scale to
  more than one instance, and do not put the voice WebSocket behind round-robin balancing, without sticky sessions and
  moving those to Redis.** Deploys drop in-flight calls: deploy outside calling windows.

## 1. Staging checklist

- [ ] `alembic upgrade head` on a restored copy of the production database; `validate_migrations.py` on a scratch DB.
- [ ] `GET /diag/voice-config` has no `error`s; review each `warning`.
- [ ] Probes run with approval: `probe_realtime_cancel.py`, `probe_hangup.py` (hangup and delete-recording, both providers).
- [ ] A test org exists with its own credentials and a test phone number; no customer org is touched.
- [ ] Log scan: no phone numbers or transcripts where `LOG_PRIVACY_ENABLED` should mask them.

## 2. Flag activation order (one step, one soak, one review)

Each step: set the flag for the **test org / staging only**, run the scenarios listed, run `voice_postcall_verify.py`,
read `voice_latency_report.py`, soak for at least a day of test traffic, then proceed. Revert on any failure.

| Step | Flag | Scenarios | Gate to continue |
|------|------|-----------|------------------|
| 1 | `VOICE_LATENCY_TRACKING`, `LOG_PRIVACY_ENABLED` (already default on) | S01–S03 | metrics appear, no PII in logs |
| 2 | `VOICE_METERING_V2` | S17c | voice_seconds within 10 s; no duplicates; a mid-call kill loses ≤ 1 checkpoint |
| 3 | `VOICE_GREETING_AI_DISCLOSURE` | S08 | every greeting names the AI (org/legal sign-off on wording) |
| 4 | `VOICE_ELEVENLABS_PREWARM` | S07 | first-audio p50 not worse |
| 5 | `VOICE_NONBLOCKING_TTS_FINISH` | S07 | no truncated answers |
| 6 | `VOICE_GREETING_AUDIO_CACHE` | S08 | greeting plays cleanly (skipped automatically in consent mode) |
| 7 | `VOICE_SAFE_CANCEL` | probe, S04–S06 | **only if the probe shows cancel keeps the session open** |
| 8 | `VOICE_SMART_TURN_TAKING` | S04, S05, S10 | backchannels do not cut the agent off |
| 9 | `VOICE_TRIM_INTERRUPTED_CONTEXT` | S04 | agent does not repeat/skip content after a cut-off |
| 10 | `VOICE_TTS_RECOVERY` | S15 | silence never reaches the caller |
| 11 | `VOICE_RECONNECT_RECOVERY` | S12, S16 | no duplicate bookings |
| 12 | `VOICE_END_CALL_ENABLED` | probe hangup, S13, S14 | provider confirms hangups; no premature hangups in 50+ calls |
| 13 | org `voice_compliance` policy | S17a/b | approved by the org (see `voice-compliance-review.md`) |
| 14 | `VOICE_RETENTION_WORKER_ENABLED`, then `VOICE_RETENTION_DELETE_PROVIDER_AUDIO` | S17b | first run on the test org only; confirm the provider's copy is gone |

## 3. Pilot (one org)

Pick one real org that has agreed, with a named contact. Enable steps 1–6 first; add the others one at a time over
days. Keep the flags per-process (env) for global ones; per-org items (profile, greeting, brand, compliance, enhanced
dialogue) are set in that org's settings, so other orgs are unaffected.

## 4. Monitoring during the pilot

- `GET /diag/voice-latency`, `scripts/voice_latency_report.py`: first-audio p50/p95/p99, cancel time, TTS connect.
- Log events to alert on: `voice_hangup_failed_line_may_be_open` (page someone), `voice_tts_recovery_failed`,
  `voice_cancel_suspected_session_close`, `openai_ws_reconnect_exhausted`, `voice_usage_checkpoint_failed`,
  `voice_retention_provider_delete_not_done`, `voice_config_issue` (startup).
- Provider consoles: open calls that should have ended; spend on OpenAI, ElevenLabs, Plivo/Twilio.
- Host: api container memory (~280 MB normal on t3.medium), load, disk (`docker builder prune -af`).
- Daily: reconcile `voice_seconds` ledger rows against the carrier's call durations for the pilot org.

## 5. Rollback

1. Turn off the newest flag (env or org setting) and restart the api container (`docker compose up -d api`); each flag's
   off-state is the previous behaviour, covered by tests.
2. If code is at fault: redeploy the previous image tag (the GitHub Action deploys `main`; revert the merge).
3. Migrations need **no** rollback for a code revert (columns are nullable and ignored). To remove them anyway:
   `alembic downgrade d0f6b4c8e2a5` (drops the voice columns and their data).
4. Open calls at the carrier: use `scripts/probe_hangup.py … hangup <uuid>` or the provider console.
5. Recordings already deleted at the provider cannot be restored: this is why the delete flag is last and test-org first.

## 6. Criteria for production release

All of: full suite green in CI; staging migrations validated on PostgreSQL; both probes pass; every scenario S01–S17
run with no failed automated check and listening scores recorded; a pilot of at least one week and 200 calls with no
`line_may_be_open`, no duplicate bookings, no usage discrepancy above 2 %; latency p95 within target agreed with the
business; compliance items in `voice-compliance-review.md` signed off by the people named there; a rollback rehearsed.
