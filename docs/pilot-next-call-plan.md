# Next controlled outbound call: PLAN (nothing has been run or changed)

Goal: one short call that tests **only** `VOICE_RECONNECT_RECOVERY` as a newly enabled behaviour, with the first-audio
instrumentation (`tts_chars_at_first_chunk`) and the Phase 8D socket/response fixes already in the code.

## Entry conditions (all must be true before any call)

1. Business content approved by its owner and written to the pilot org ([preview](pilot-business-config-preview.md)); read back.
2. One complete API run and the web unit run green on the working tree you intend to use (see the Phase 8F report).
3. The pilot API is started from `.env.pilot` through `VEEROX_ENV_FILE`; `GET /diag/voice-config` shows no errors.
4. You have set, and checked in each provider's console, the spending protections in "Spending" below.
5. You are ready to answer, and you have decided whether the Plivo production line may appear as caller ID (see Blockers).

## Exact settings (pilot file only; lines to add or change after approval)

| Setting | Value | Why |
|---|---|---|
| `VOICE_RECONNECT_RECOVERY` | `true` | the one new behaviour under test |
| `VOICE_BRAND_NAME` | `Veerox` | the prompt names the brand consistently |
| `PILOT_ALLOWED_NUMBERS` | **only your own number** | the two agent lines are not valid destinations |
| `PILOT_MAX_OUTBOUND_CALLS` | `1` | the process refuses a second call attempt |
| `VOICE_OUTBOUND_TIME_LIMIT_SECONDS` | `120` | sent to Plivo as `time_limit` (and to Twilio as `TimeLimit` if it fails over) |
| `VOICE_OUTBOUND_RING_TIMEOUT_SECONDS` | `25` | sent as `ring_timeout` (Twilio `Timeout`) |
| `MAX_CONCURRENT_CALLS` | `1` | |
| Unchanged | recording `off`, `LOG_TRANSCRIPTS=false`, AI disclosure on, `VOICE_METERING_V2=true`, all other voice flags off, no Plivo webhook change, workers off | |

Launch: `python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8002 --no-access-log`, then `ngrok http 8002` on the configured
host. Place the call once with `POST /admin/outbound/call` and the pilot admin token.

## Call script

Follow [pilot-first-call.md](pilot-first-call.md). To exercise recovery you cannot (and must not) break a paid call on purpose; if a
reconnect happens by itself, that is the observation. The offline fault-injection tests are the primary reconnect check.

## Safeguards: what is enforced and what is not

| Safeguard | Status |
|---|---|
| Destination allowlist | enforced in code; tested |
| One call per process | enforced in code; tested (resets if the API restarts) |
| Maximum call length 120 s | **requested from the provider** using Plivo's documented `time_limit`; tested only that the parameter is sent. Whether Plivo ends the call at that moment is **unproven until a live call** |
| Ring timeout 25 s | same: requested, unproven live |
| No recording | enforced (org policy `off` and pilot default `off`); verified in the database |
| Spending cap | **not enforced by this software, and no ₹100 cap should be assumed.** There is no budget reservation or hard cost ceiling in the code path. |

### Spending

The code bounds the **quantity** (one call, 120 s requested), not money. Measured usage from the two earlier calls (about 280 s of
voice in total) was 141,173 Realtime input tokens, 2,818 output tokens, 3,840 ElevenLabs characters and 255 s of transcription, so
a 120 s call is expected to use roughly half of that per call. Prices are not in this repository and are not asserted here: multiply
those quantities by the current price on each provider's page (see [pilot-controlled-call-checklist.md](pilot-controlled-call-checklist.md)
for the earlier assumptions, which are unverified).

Money protection you must put in place yourself, per provider, and confirm in its console before the call: an OpenAI project budget or
spend limit on the key, ElevenLabs plan credits/limits, and the Plivo account balance (a prepaid balance is a real ceiling; a postpaid
account is not). The same OpenAI and ElevenLabs keys as the main `.env` are in the pilot file, so a limit there also limits production.

## Success criteria (from logs and the database)

- No `voice_stray_connection_closed`-driven reconnects; if a reconnect happens, the call continues with its context
  (`reconnect_recovery_ms` present, the model does not re-greet or ask the caller to repeat what was already answered).
- No duplicate tool action; no tool claimed done before success; no meeting offered after a refusal.
- First-audio p50 and the new `tts_chars_at_first_chunk`, to separate provider slowness from ElevenLabs waiting for text.
- Call ends within the requested limit; no unmasked number in the log; recording none.

## Abort criteria

The agent states a price or figure, does not disclose it is an AI, contacts any number but yours, a second call is attempted, the
line stays open after you hang up, or any unmasked number or secret appears in the log. Hang up on your phone, then stop the API.

## Rollback

1. Hang up; stop the API (Ctrl+C) and the ngrok process.
2. Remove `VOICE_RECONNECT_RECOVERY`, `VOICE_OUTBOUND_*`, `PILOT_MAX_OUTBOUND_CALLS` and `VOICE_BRAND_NAME` from `.env.pilot` (all default to off or unset).
3. If a call is stuck open: `python scripts\probe_hangup.py plivo hangup <call_uuid> --i-approve-live-telephony`.
4. Engage the pilot Redis kill switch if needed: `POST /admin/kill-switch` with `{"enabled": true}`.
5. Business content: use the rollback section of the [preview](pilot-business-config-preview.md); or delete the Neon project.
6. Nothing at Plivo or Meta was changed, so there is nothing to restore there.

## Blockers still open

- Business content is a synthetic preview awaiting approval and a database write.
- The Plivo line is attached to the **production** application; an outbound call from it shows that number as caller ID and uses the
  production account's balance. Prefer a separate pilot number.
- The pilot and main environments share OpenAI, ElevenLabs, Plivo and Twilio credentials and the ngrok host.
- Credential rotation was waived by the owner, not done.
- Provider-enforced duration, reconnect recovery and the Phase 8D fixes are only offline-verified.

## Approvals required, each separately

1. Writing the business configuration to the pilot database.
2. Editing `.env.pilot` with the settings above.
3. Starting the API and tunnel.
4. The single paid call (and confirmation that provider spending limits are set).
