# Phase 8J — replay remediation

21 October 2026. **Live-pilot readiness: BLOCKED.** Offline source changes only; no call is authorized. This report supersedes the replay findings in Phase 8I. Test results are recorded below when the running full suite finishes.

## Root causes and changes

**WebSocket:** `stream_allowed` previously used an incrementing Redis counter with a configured allowance of two. Both connections could enter the bridge concurrently. The counter had no session owner, no release operation, and no relationship to socket cleanup.

The existing authentication module now verifies the HMAC, admission expiry, organization, call, caller, campaign and provider, then atomically claims exclusive ownership of the authenticated organization/call. Ownership applies across separately issued tokens for the same call. Redis Lua checks the initialized state, capacity and existing owner before claiming. A random internal owner identifier permits only that owner to release its entry. Tokens and caller numbers are never stored in ownership fields or logged. Claim encodings use unambiguous JSON arrays rather than separator concatenation.

The bridge releases ownership only after its authorized handler and cleanup finish, including disconnects, exceptions and cancellation. Pending OpenAI connection tasks are cancelled, awaited and closed even when a malformed handshake fails before the previous cleanup boundary. The OpenAI event pump is also cancelled and awaited on teardown. Authenticated identities cannot be overwritten through a Plivo start frame. Production `auto` still resolves to `off`, and legacy identity handling remains available in that mode.

A replacement must present a valid, unexpired token with the same authenticated bindings after the previous owner has released. This remains a bearer authorization: possession of that token permits a sequential replacement until expiry; the change prevents overlap rather than making the token permanently single-use. A second concurrent connection receives close code 1008 before any AI connection starts. A stale owner's release cannot remove a replacement owner's entry. Existing OpenAI reconnection inside one telephony stream keeps its ownership throughout; its history, interruption and business-action recovery remain unchanged.

**Webhook:** replay protection previously performed separate `INCR` and `EXPIRE` calls and forgot a nonce after 3,600 seconds. A captured request still had a valid provider signature after eviction and became acceptable again. The two operations were also not atomic.

The replacement is an atomic, durable, fixed-capacity exact replay ledger. The provider nonce is fingerprinted once in the isolated pilot/security-key namespace, independently of which valid V3/MA-V3 signature verifies. Captured multi-signature requests therefore cannot replay by switching the verifying credential during token rotation. The same nonce cannot bypass detection through another path, query ordering, account route or worker. Plivo documents nonces as unique per request; a provider collision would conservatively reject the colliding request. Only verified requests reach storage. Entries never expire. At 100,000 accepted nonces, further new deliveries fail closed. There is one bounded set, rather than an unbounded collection of per-request Redis keys. The stream ownership hash is separately capped at 10,000 owners. Both keys share a Redis Cluster hash tag. Missing state, malformed storage, capacity exhaustion and Redis errors reject admission; nothing is automatically initialized or reset.

The old `PLIVO_NONCE_TTL_SECONDS` and `PLIVO_STREAM_TOKEN_MAX_USES` settings remain accepted for configuration compatibility but no longer expire replay records or authorize multiple streams. No `.env` file or business configuration was changed.

## Protocol and availability limits

Plivo documents the V3 nonce as a random value unique for every request, not an authenticated timestamp. There is no reliable provider-supplied age to check. Consequently this implementation **cannot reject an arbitrarily delayed, valid request that has never previously been accepted**, nor establish historical freshness from the old expired cache. It does reject an accepted nonce indefinitely within the retained credential epoch. See [Plivo V3 signatures](https://www.plivo.com/docs/voice/concepts/signature-validation).

Plivo documents retryable transport/status failures, configurable retry counts and a bounded callback timeout. A retry signed with a new nonce is accepted normally; an already accepted nonce is rejected without executing the handler again. An identical signed request cannot be distinguished from a captured replay. This deliberately does not promise that a provider reusing the exact nonce/signature will receive a successful response. Nor does nonce protection replace business-event idempotency for independently signed duplicate events. See [callback configuration](https://www.plivo.com/docs/voice/concepts/callback-configurations) and [idempotent callbacks](https://www.plivo.com/docs/voice/concepts/callbacks).

The durable ledger trades availability for security. At capacity, stop and establish a new signing-credential epoch; never evict old records while their provider credentials remain accepted. Missing keys fail closed. A rollback to an older Redis snapshot can lose accepted nonces while retaining its readiness marker; after any such data loss, stop all pilot workers and rotate every accepted signing credential before establishing a new epoch. Durable storage and operational controls over Redis remain required.

Stream ownership has no expiring lease. Otherwise a paused old worker could overlap a replacement after the timeout. Ordinary socket drops release after cleanup, allowing an authenticated reconnect until token expiry (currently 300 seconds). A crashed worker, hung cleanup or failed Redis release leaves ownership locked. Recovery requires proving every old worker/socket is stopped before manually clearing that specific stale owner. There is no automatic crash takeover. Existing call history recovery across an OpenAI socket reconnect is preserved; a replacement telephony stream retains the application's existing fresh-conversation behavior.

Redis documents atomic [Lua execution](https://redis.io/docs/latest/develop/programmability/eval-intro/), and Upstash documents [EVAL support](https://upstash.com/docs/redis/sdks/py/commands/scripts/eval). The Redis scripts were executed with fakeredis and its Lua engine, not against the live Upstash store. Actual Plivo retry/stream delivery remains untested live. Production default behavior has regression coverage; no production runtime was changed or attested.

## Verification

| Area | Result |
|---|---|
| Targeted security, lifecycle, reconnect, interruption, signature and disclosure tests | PASS ? 201 passed in 45.53 seconds |
| Full API suite | Running; result pending |
| Synthetic logging verification | PASS — all 11 checks; transcript logging off, privacy enabled |
| Pilot Redis read-only connectivity | PASS — PING and ledger metadata reads only |
| Pilot security-state initialization | BLOCKED — both v2 ledgers absent; enforcement rejects until initialized |
| Neon / Upstash rotation | BLOCKED — both passwords still match pre-8H exposed values |
| OpenAI spending controls | BLOCKED — no console/project billing confirmation supplied |

Tests cover parallel stream and webhook admissions, owner-validated replacement and stale release, dropped/error/cancelled socket cleanup, malformed handshakes, expiry boundary, token tampering, cross-call/org claims, unavailable Redis, absent and saturated state, signed webhook repeats after the legacy TTL, new-nonce retries, signature/reference vectors and AI disclosure. The six stale tests were reconciled with the async/expiry interface, fresh synthetic nonces, explicit replay storage and an in-memory database. Protection was not relaxed.

The suite uses `scripts/pilot_offline_suite.py`: workspace-local temporary paths, synthetic credentials, deterministic replay-test clocks, in-memory SQLite, and an audit hook blocking external socket connections. Test-only `fakeredis[lua]` was added to the dev dependencies and lockfile; the final runs use the existing Redis Python client 7.4.0.

Safe evidence: `.validation-8j/targeted.xml`, `.validation-8j/api.xml`, `.validation-8j/credentials.json`, `.validation-8j/runtime.json` and `.validation-8j/redis-status.json`. Credential comparisons export only booleans. Redis metadata exports only readiness booleans, TTLs and aggregate counts. No raw signatures, tokens, caller numbers, provider payloads or credentials appear in this report.

## Manual actions before any call approval

1. Rotate only the isolated pilot Neon and Upstash credentials in their consoles; update `.env.pilot` securely and supply completion evidence without values. Recheck connectivity and comparison afterward. Neither password was rotated automatically.
2. Establish a fresh Plivo signing-credential epoch for the pilot, preferably with an isolated pilot account/subaccount. Every old signing token accepted for this pilot must cease being accepted before an empty replay ledger is used. The current provider account is shared with the main environment: **do not rotate shared production tokens or edit provider webhooks as part of this task.** This requires a separate approved account/credential arrangement.
3. Approve one-time initialization of the isolated pilot Redis only after every pilot worker is stopped and all previously issued stream tokens have expired. Wait at least the maximum previously configured stream-token lifetime after the last issuer stops; the current configured lifetime is 300 seconds. Confirm no old issuer is still running. Never reset an existing ledger to make a call connect.

   The prepared command below was **not executed**. It requires all three explicit attestations, refuses a production/shared Redis target, atomically initializes both keys only if neither exists, and refuses overwrite:

   ```powershell
   .\.venv\Scripts\python.exe scripts\pilot_security_initialize.py --confirm-all-plivo-signing-tokens-rotated --confirm-all-old-stream-tokens-expired --confirm-all-pilot-workers-stopped
   .\.venv\Scripts\python.exe scripts\pilot_security_status.py
   ```

   Expected after approved setup: both readiness flags true and both TTLs `-1`. Verify Upstash durability, capacity and that no eviction/restore procedure can silently roll replay state backward. If state is unavailable, keep the pilot stopped.

4. In OpenAI's console, confirm the pilot key's project, the configured spend amount, whether hard enforcement is enabled, current usage/headroom, and organization-level billing/credit availability. No project/account limit was changed. Budget alerts alone are insufficient. Shared production limits must stay untouched; use an approved isolated project if changes are needed. Provider enforcement can lag and does not create an exact per-call cross-provider monetary ceiling. See [OpenAI spending limits](https://developers.openai.com/api/docs/guides/spend-limits).
5. Reconfirm ElevenLabs credits/overage and Plivo balance/auto-recharge before approval. The Phase 8I metadata snapshots are historical: ElevenLabs 109,551 reported units remaining with extension disabled; Plivo USD 17.20986 prepaid and auto-recharge off. They were not refreshed with provider requests during 8J. Confirm the sole allowlisted handset, cost ceiling and exactly one outbound attempt separately.

File-resolved settings remain: pilot enforcement `enforce` via `auto`; reconnect recovery and existing live turn detection on; smart turn-taking and TTS recovery off; AI disclosure on; one outbound attempt per process, one concurrent call, requested 120-second limit and 25-second ring timeout; workers and provider configuration changes off. No experimental Phase 7 flags or business records were changed. Enhanced dialogue's previously audited off state was not rewritten or newly attested from a running service.

## Rollback and stop condition

Keep the pilot stopped while any blocker remains. Do not downgrade signature enforcement, restore expired replay caching, delete replay state, restore exposed passwords, restart to reset the one-call budget, or enable feature flags. Preserve the local changes and evidence for review. If a future authorized run fails authentication, hang up/terminate that one carrier call, stop its API and tunnel, and investigate offline; no automatic redial.

No paid API requests, live calls, tunnel exposure, provider webhook changes, production database writes, deployments, commits or pushes were made. The only remote account operation during 8J was read-only pilot Redis health inspection. **Stop for approval.**
