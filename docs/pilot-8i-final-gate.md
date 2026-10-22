# Phase 8I — final live-pilot gate

21 October 2026, checks around 16:58 IST. **Overall: BLOCKED. No call is authorized.** Security verification also reports FAIL. This supersedes the permissive startup/rollback choices in the Phase 8H runbook.

Claude context was read first from the latest project conversation. Its last completed Phase 8H report already marked credential rotation and spending verification blocked. The subsequent saved signature implementation added expiry and Redis replay accounting after its existing tests were written. This report checks that newer source, not the earlier claimed PASS count.

## Area results

| Area | Verdict | Finding |
|---|---|---|
| Neon / Upstash credential rotation | **BLOCKED** | Both current passwords match exposed values in the pre-8H local conversation. Boolean comparisons only; no secret values, hashes, or connection strings were exported. The earlier rotation waiver does not satisfy this phase's explicit requirement. |
| Connectivity and data isolation | **PASS** | PostgreSQL read-only transaction succeeded and matched the declared pilot database. Redis `PING` succeeded. DB and Redis targets differ from the main env. No data-store writes were made. |
| Webhook / WebSocket security | **FAIL** | Positive expiry/binding/comparison/logging checks pass, but strict replay checks fail; the existing security suite also has stale interfaces and missing replay-store fixtures. See below. |
| OpenAI spending policy | **BLOCKED** | No console/admin billing access. A model key does not attest project spending controls. No generative/billable probe was attempted. |
| ElevenLabs quota and overage setting | **PASS for read-only snapshot** | Creator plan: 32,869 used of 142,420 reported character units; 109,551 remaining. `max_credit_limit_extension=0`, both extension flags false. Per-key quota and current account selection still need owner confirmation. |
| Plivo balance / auto-recharge | **PASS for read-only snapshot** | Prepaid, USD 17.20986 cash credits, auto-recharge false. This is the shared production account/caller ID, not a dedicated ₹100 budget. |
| Synthetic business configuration | **PASS** | Correct `Veerox Pilot` org selected by pilot ID. Stored default voice script, greeting and seven active knowledge items match the reviewed fixture. Actual voice pipeline loaders read that script and greeting. |
| Configured call limits / feature flags | **PASS offline** | Values below match the requested baseline. No serving call session was started or attested. |
| Only operator handset authorized | **PASS for one configured entry; owner confirmation pending** | Exactly one destination is configured. Its value was not displayed. Owner must confirm it is the handset currently held; this audit cannot establish physical ownership. |
| No provider webhook changes | **PASS** | Changes disabled in settings; startup registration and workers off. Only provider account/subscription GETs were made, with no settings mutation. |

The OpenAI, ElevenLabs and Plivo account credentials are shared with the main env. Do not change those account-wide limits to run this pilot. The tunnel host is also shared: the main dev service/tunnel must not be serving that address during a future pilot.

The ElevenLabs API documents zero credit-limit extension as usage-based billing disabled. The Plivo API defines the reported balance as USD and exposes prepaid/auto-recharge state. These are account snapshots, not future guarantees or a software spending cap. Sources: [ElevenLabs subscription fields](https://elevenlabs.io/docs/api-reference/user/subscription/get), [Plivo account fields](https://docs.plivo.com/docs/account/api/account).

## Security audit and offline evidence

The signature implementation matches the six SDK reference vectors in the existing tests. It uses HMAC-SHA256 and `hmac.compare_digest` on bytes. The stream token binds call ID, caller, organization, campaign, provider and expiry. Tampering with each bound field is rejected. Expired/missing tokens and unavailable replay storage fail closed. Current `PUBLIC_BASE_URL` is HTTPS, has a root path and has no userinfo, query or fragment; signatures reconstruct the public URL instead of trusting an incoming Host header. Logs redact signed query values and token/nonce fields. Production `auto` mode remains `off`, preserving legacy routing behavior.

**Current enforcement is still `auto → enforce` for the pilot. Nothing was changed to `warn` or `off`.** A genuine provider rejection is a stop condition, not permission to disable authentication.

Two reproduced replay limits prevent a strict security PASS:

1. `PLIVO_STREAM_TOKEN_MAX_USES` resolves to **2**. The exact same valid bearer token can open a second stream while still unexpired. There is no exclusive active-stream lease in this token check. This is documented in code as tolerating one reconnect; it does not distinguish a sequential reconnect from a simultaneous replay. One outbound-call counter does not provide this stream exclusivity.
2. A verified webhook nonce is remembered for **3,600 seconds**. Once its record expires, the same captured signed request is accepted again: signatures have no independently checked freshness/expiry. The replay defense is therefore bounded by Redis retention, not permanent rejection of an already processed call. Cache loss has the same limitation.

Stream-token lifetime is **300 seconds**. The nonce `INCR`/expiry operations are separate; failures reject in enforce mode. A fail-closed fake-store test covers outage behavior, but the live store's write operations were not exercised because this audit used only `PING`.

Verification results:

- Existing security and neighboring suites: **118 passed, 6 failed**, 63.58 seconds. Four failures are valid-signature endpoint tests that never supplied the new fake replay store (`nonce_store_error`); two call the obsolete pre-expiry stream API. These failures are test/source drift, not proof that real provider traffic is invalid. They still prevent claiming the existing suite is green.
- Independent current-interface gate tests: **13 passed, 2 failed**, 0.87 seconds. The failures explicitly reproduce the two strict replay properties above. They are not skipped or marked expected-failure.
- The standalone eleven-case logging verifier reports **PASS**. The final combined targeted run reports **196 passed, 8 failed** in 60.99 seconds: the same six stale-suite failures plus the two strict replay findings. These runs overlap; do not add their counts as distinct tests. No full application suite was required or rerun for this final gate.

Resolve replay policy/implementation and refresh the stale security fixtures before clearance. A legitimate Plivo retry may need idempotent handling; do not simply disable the guard when changing retry behavior. Prefix-stripping proxy deployments remain outside this root-path pilot check. No real Plivo webhook or active call has been exercised here.

## Verified pilot configuration

| Item | Observed |
|---|---|
| Org | `Veerox Pilot`, selected by configured pilot org ID |
| Business content | One matching default voice script; matching Hindi/Hinglish greeting with AI disclosure; seven matching knowledge items |
| Conversation / voice decision mode | `strict_script` / `shadow` |
| Enhanced dialogue | `false`, no published scenario version; legacy voice script is installed and loaded |
| Recording | `off` |
| AI disclosure | `true`, also present in the stored greeting |
| Outbound limit | 1 attempt per process; a failed attempt consumes it |
| Concurrent-call setting | 1; only one API process may be used |
| Provider time limit | 120 seconds requested via Plivo `time_limit` |
| Ring timeout | 25 seconds requested via `ring_timeout` |
| Reconnect recovery | `true` |
| Existing live turn detection | `true` |
| Smart turn-taking | `false` |
| TTS recovery | `false` |
| Voice profile / normalization | No profile; normalization off |
| Meta / Brevo / Twilio | Environment credentials absent; org Meta/Twilio credentials absent |
| Workers / provider config changes / Plivo startup registration | All off |
| Transcript logs / privacy | Off / on |

No env, feature, org or provider settings were changed. Reconnect recovery opens replacement AI sessions within its bounds; it is not another outbound dial, but can incur additional provider usage. The 120-second parameter is verified in mock requests; actual provider enforcement remains unproven until an authorized call. The per-process counter resets on restart and is not an account-level spending guard.

## Remaining manual actions

1. **Rotate the pilot Neon and Upstash passwords yourself**, in their consoles. Neon: select the pilot project/branch and role, reset its password, then replace only the pilot `DATABASE_URL`. Upstash: select the pilot Redis database, reset/rotate its password, then replace only the pilot `REDIS_URL`. Do not paste values into chat. Do not rotate shared production accounts or revert to the old passwords. Report completion so rotation comparison and connectivity can be rechecked; current connectivity alone is insufficient.
2. **Resolve the two replay findings and the six stale security-test failures.** Preserve enforcement and legitimate-request compatibility; rerun the current-interface gate and neighbor tests. No bypass/waiver is assumed.
3. **OpenAI console:** identify the project owning the pilot key, then Project settings → Limits → Spend. Confirm the monthly limit amount, whether “Enforce a hard limit” is enabled, this month's spend/headroom, alerts, and the organization usage/credit limit. Report only non-secret values and whether production shares the project. Do not change shared limits. Alerts do not stop traffic, and even hard-limit enforcement can lag; [official OpenAI guidance](https://developers.openai.com/api/docs/guides/spend-limits) distinguishes the controls.
4. **ElevenLabs console:** confirm this is the intended workspace/key, its remaining allocation, key-specific usage restriction, and overage remains disabled. The API snapshot already reports zero credit extension. Do not enable overage or buy credits for this check.
5. **Plivo console:** confirm the shared caller ID/account is intentionally permitted for this one call, prepaid balance remains adequate, auto-recharge is still off, and outbound destination permission permits the operator's handset. Do not change the production line's application/webhook or shared billing controls.
6. Confirm the one allowlisted number is the phone you hold, accept the absence of a cross-provider ₹100 hard cap, and provide **explicit authorization for exactly one outbound attempt** after the other gates pass. No retry or restart is covered by that authorization.

## Exact future startup procedure — NOT EXECUTED, BLOCKED NOW

Run only after all blockers are resolved and explicit authorization is received. Stop the main dev API/tunnel first because the public hostname is shared. Use one local API process, no `--reload`, no extra workers. Startup performs existing pilot bootstrap commits, so this procedure is not a read-only check; it was deliberately not run during this audit.

Window A:

```powershell
Set-Location 'D:\adrsproject\veerox-'
$env:VEEROX_ENV_FILE = (Resolve-Path -LiteralPath '.env.pilot').Path
$env:PLIVO_SIGNATURE_MODE = 'enforce'
.\.venv\Scripts\python.exe scripts\pilot_preflight.py .env.pilot
if ($LASTEXITCODE -ne 0) { throw 'Pilot preflight failed; do not start.' }
.\.venv\Scripts\python.exe scripts\pilot_8i_readonly.py
# Inspect the safe facts: correct isolated stores/org/content and exact flags.
# Confirm port 8002 is free before the next line.
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8002 --workers 1 --no-access-log 2>&1 | Tee-Object -FilePath 'pilot-8i-call.log'
```

Before exposing it, verify the serving process with a read-only call whose admin key is never printed:

```powershell
.\.venv\Scripts\python.exe -c "from apps.api.config import settings; import httpx; r=httpx.get('http://127.0.0.1:8002/diag/voice-config', headers={'X-Admin-Token':settings.admin_token}, timeout=5, trust_env=False); r.raise_for_status(); d=r.json(); f=d.get('flags',{}); assert d.get('webhook_signature_mode')=='enforce'; assert f.get('voice_reconnect_recovery') is True; assert f.get('voice_smart_turn_taking') is False; assert f.get('voice_greeting_ai_disclosure') is True; assert f.get('log_transcripts') is False; print({'webhook_signature_mode':d.get('webhook_signature_mode'),'flags':f})"
```

Expected: enforcement `enforce`, reconnect recovery on, smart turn-taking off, AI disclosure on, transcript logs off. The current diagnostic endpoint does **not** expose live turn detection, enhanced dialogue, per-run count, or duration settings: the read-only audit establishes their file/DB values, not their identity in an already running process. Preserve this limitation in the approval record; serving-process attestation for those values remains pending. If any check differs, stop rather than changing features to make the call connect. Do not use `/diag/openai-realtime`, `/diag/latency`, voice previews, or other generation probes.

Window B, with the same pilot env-file selection:

```powershell
Set-Location 'D:\adrsproject\veerox-'
$env:VEEROX_ENV_FILE = (Resolve-Path -LiteralPath '.env.pilot').Path
$pilotPublicBase = (& .\.venv\Scripts\python.exe -c "from apps.api.config import settings; print(settings.public_base_url)").Trim()
ngrok http 8002 --url $pilotPublicBase --inspect=false
```

This uses the existing public base, with no Plivo webhook mutation and local HTTP inspection disabled to avoid retaining signed URLs/headers. The command follows the [ngrok CLI reference](https://ngrok.com/docs/gateway/agent/cli). Check the assigned HTTPS hostname equals the configured base. A changed/occupied hostname is a stop condition, not permission to edit production routing.

**Call trigger remains withheld until explicit authorization.** The planned request is one `/admin/outbound/call` for the configured allowlisted handset and provider `plivo`, under the pilot admin identity. Do not submit it while the final gate is blocked. Any failed attempt still uses the single allowance. Do not restart to get a second attempt.

## Expected call behavior

One short Veerox AI greeting, natural Hindi/Hinglish, fictional/approved facts only, no prices or guarantees. Use the current script's thinking pause, corrected budget and continuity question, one interruption, a callback time supplied by the tester, and honest human escalation. Smart turns and enhanced dialogue remain off, so do not score their opt-in behavior as active. Callbacks/handoff may create pilot records; workers are off and Meta/Brevo/Twilio cannot send notifications. The agent must not claim a human has joined or a message was sent when neither happened.

Avoid reusing an occupied appointment slot. The safest short call focuses on callback/handoff instead of booking another appointment. Finish and hang up voluntarily around 100–110 seconds; 120 seconds is the requested backstop, not permission to keep talking until an unverified cutoff. If a signed webhook is rejected, stop and diagnose it offline—never switch to warn/off or automatically redial. Realtime reconnection should restore only this call's history and avoid duplicate actions; that behavior is offline-verified, not yet proven with live Plivo.

## Shutdown and rollback

1. Tester hangs up first. If still connected, end that one call in the Plivo console and verify terminal status. Stopping a WebSocket alone may leave a `keepCallAlive` carrier leg open.
2. Ctrl+C Window A (API), then Ctrl+C Window B (tunnel). Verify port 8002 has no listener and the pilot ngrok process is gone. Do not kill unrelated Python processes.
3. Do not restart either process or place a second call. Retain sanitized logs and compare provider usage before/after. Logs may require further review before sharing; never paste env files, raw headers, signed URLs, or raw provider bodies.
4. Rollback means keeping the pilot stopped. Preserve enforcement, the one-call limit, duration settings, disabled integrations and worker-off state. Do **not** delete safety settings, restore exposed credentials, re-enable Meta/Brevo/Twilio, delete business data, change shared limits, or repoint webhooks. Any code/config rollback requires a separately reviewed local snapshot and separate approval.

## Evidence and changes in this verification

Safe evidence: `.validation-8i-credentials.json`, `.validation-8i-readonly.json`, `.validation-8i-logging.json`, `.validation-8i/initial.xml`, `.validation-8i/gate.xml`, and `.validation-8i/final.xml`. Only allowlisted fields/booleans are exported; no raw provider payloads or actual customer transcripts were copied into reports.

Added `scripts/pilot_8i_readonly.py`, current-interface gate tests, and this report; annotated the older runbook as superseded. Application source, `.env.pilot`, provider account settings and business configuration were not changed by this Phase 8I verification. No API server, ngrok tunnel, generative request, outbound call, deployment, commit or push was initiated. Ordinary read-only database/authentication operations and two GET metadata endpoints were used.

**Final gate remains BLOCKED. Wait for manual actions and a new explicit single-call authorization.**


---

## Update, 22 October 2026 (about 10:00 IST): re-verification of the current source

**Overall: still BLOCKED. No call is authorized.** What changed since the section above: the replay design was reworked (a permanent fixed-capacity nonce ledger and one exclusive owner per authenticated call, claimed and released atomically in Redis), so both strict replay findings are now closed in code and the security suites are green. What did not change: the credentials, the manual spending confirmations, and a new blocker that the rework introduced.

| Area | Verdict now | Evidence |
|---|---|---|
| Neon / Upstash rotation | **BLOCKED** | Both current passwords still match the values exposed in the conversation (boolean comparison only). `.env.pilot` connectivity is fine (PostgreSQL read-only `SELECT`, Redis `PING`), which does not substitute for rotation. |
| Webhook / WebSocket security, offline | **PASS** | `test_voice_webhook_signature.py` + `test_pilot_8i_gate.py`: 67 passed, including expiry, binding of every claim, replay of a captured webhook after nonce eviction, a captured stream token opening a second session, replay-store outage, constant-time comparison, `PUBLIC_BASE_URL` handling, log redaction and production `auto` = legacy. Signatures are pinned to Plivo's own SDK output. |
| Replay state in the pilot Redis | **BLOCKED** | `scripts/pilot_security_status.py` (read-only): `nonce_state_ready=false`, `stream_state_ready=false`. With enforcement on, an uninitialised ledger rejects every request. I ran the real claim code against the pilot Redis with a throwaway id: EVAL works there, both claims return "state missing" before any write, and the database size was unchanged. **A real call attempted now would be refused at answer and would still consume the one-call allowance.** |
| Spending (read-only snapshot, 22 Oct 04:26 UTC) | **BLOCKED on manual confirmation** | ElevenLabs Creator: 33,024 of 142,420 character units used, `max_credit_limit_extension=0`, extension flags false. Plivo: prepaid, USD 17.20426, auto-recharge off. OpenAI: not accessible without console access. All three accounts are shared with production; there is no hard monetary cap. |
| Pilot call configuration | **PASS offline** | `scripts/pilot_8i_readonly.py`: effective signature mode `enforce`; one allowlisted destination; one call per process, one concurrent; `time_limit` 120 s and `ring_timeout` 25 s in the provider request; reconnect recovery and live turn detection on; smart turn-taking, TTS recovery, enhanced dialogue off; Meta, Brevo and Twilio absent from the environment and from the org; recording off; AI disclosure on; reviewed script, greeting and seven knowledge items match; no provider config changes, workers off. |
| Targeted regression | **PASS** | Pilot, 8F/8H, call-safety, fresh-call, live-turn-stats, turn-controller, metering, interruption, reconnect, call-control, compliance, failover, recording, privacy, phase 6, apply-config and preview suites: 580 passed, 0 failed (plus the 67 above). |

### Why initialisation is the owner's decision

`scripts/pilot_security_initialize.py` creates the ledger, and refuses to run unless three statements are confirmed: all Plivo signing tokens were rotated, all old stream tokens expired, and the workers are stopped. The first is not true, and rotating the Plivo token would change a **shared production** credential, which this pilot must not do. The reason for the rule is real: a signed answer request captured before the ledger existed (for example in a local tunnel inspector from the earlier live calls) would be accepted once against an empty ledger, and the response to it carries a fresh stream token. So I did not run it and I will not claim the confirmations.

Choices for the owner, in order of preference:

1. **A dedicated Plivo subaccount for the pilot** (its own auth token and number). The shared-balance and production-caller-ID problems go away too. Heavier: a provider-console change that I may not make. Set `PLIVO_AUTH_ID` / `PLIVO_AUTH_TOKEN` for it in `.env.pilot`, then initialise with all three confirmations true.
2. **Accept the residual risk in writing**, and authorize a small code change that adds an explicit, logged waiver option to the initialiser (instead of asserting the rotation confirmation). The risk is limited to signed requests that were captured outside this process before today and are replayable once.
3. Rotate the shared production Plivo token. Not recommended and not authorized.

Until one of these is done, enforcement must stay on and the call must not be placed.

### What I changed in this update

This addendum and `docs/pilot-8h-call-runbook.md` (removed the `warn` option, added the replay-state and single-attempt gates, clarified the script's date note). No environment, org, provider, Redis or code change. No server was started, no tunnel, no call, no paid request.
