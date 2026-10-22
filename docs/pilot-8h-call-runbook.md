# Single controlled outbound call: startup, shutdown, rollback (PREPARED, NOT RUN)

> Superseded for the final gate by [Phase 8I](pilot-8i-final-gate.md). Do not use the `warn` option or the permissive rollback below. Phase 8I requires rotated pilot credentials, preserved signature enforcement, and explicit single-call authorization; current status is BLOCKED.

Nothing here has been executed. It supersedes the "Launch" and "Rollback" parts of [pilot-next-call-plan.md](pilot-next-call-plan.md)
for this call; the call script in [pilot-first-call.md](pilot-first-call.md) assumed `VOICE_SMART_TURN_TAKING` and
`enhanced_dialogue` on, which this call deliberately leaves **off**, so use the short script at the end of this page.

## 0. Gates (all must be true; each is a separate approval from the owner)

1. The Neon and Upstash credentials are rotated (steps at the end of this page), or you have written "I accept using the exposed credentials".
2. You have worked through [pilot-spending-safeguards.md](pilot-spending-safeguards.md) and written down which provider has a limit that actually stops spending. The software cannot do this and there is no hard monetary cap.
3. You accept that the call shows the **production** Plivo number as caller ID and uses the production Plivo balance.
4. Nothing else is listening on port 8002 and the main dev API is stopped (the pilot and the main `.env` share one tunnel host).
5. You are holding the phone whose number is the only entry in `PILOT_ALLOWED_NUMBERS`.
6. `python scripts\pilot_security_status.py` shows `outbound_call_ready: true` (the stream-ownership state exists; see the signature section below).
7. You have given explicit written authorization for exactly one outbound attempt. A restart, a retry or a second attempt needs a new authorization.

## 1. Start

```powershell
cd D:\adrsproject\veerox-
$env:VEEROX_ENV_FILE = (Resolve-Path .env.pilot).Path          # the process then reads ONLY the pilot file
python scripts\pilot_preflight.py .env.pilot                     # expect: 0 failed (4 known warnings)
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8002 --no-access-log 2>&1 | Tee-Object pilot-call.log
```

Never add `--reload` or `--workers`: a reload or restart resets the one-call counter. Then start the tunnel, in a second
window, for the host in `PUBLIC_BASE_URL`, pointing at port 8002 (the same way you normally run it). Check, without printing any secret:

- `GET http://127.0.0.1:8002/diag/voice-config` -> `ok: true`, `webhook_signature_mode: enforce`, `voice_reconnect_recovery: true`, every other voice flag false except the three defaults (`latency_tracking`, `greeting_ai_disclosure`, `metering_v2`).
- The log contains `pilot_background_workers_disabled` and no `pilot_*` error.

## 2. Place the call (once)

`POST /admin/outbound/call` with the pilot admin token and body `{"to_phone": "<your number>", "provider": "plivo"}`.
A second POST, a retry, a tool-initiated callback or a restart-less repeat is refused by the process (`limit of 1`). If the first
request fails at Plivo it still counts: restart the API only if you have decided to spend a second attempt.

Requested from Plivo: `time_limit` 120 s, `ring_timeout` 25 s. Whether Plivo ends the call at 120 s is unproven until this call.

## 3. During the call: stop immediately if

The agent states a price or figure, does not say it is an AI, calls or messages anyone but you, the line stays open after you
hang up, or the call goes past about 130 s. Hang up on your phone first. If the line is stuck:
`python scripts\probe_hangup.py plivo hangup <call_uuid> --i-approve-live-telephony`, then stop the API.
The global stop is `POST /admin/kill-switch` with `{"enabled": true}` (pilot Redis).

## 4. Shut down (always, even after a good call)

1. Ctrl+C the API window; close the tunnel window.
2. `Get-NetTCPConnection -LocalPort 8002 -State Listen` returns nothing; no `ngrok` process remains.
3. Keep `pilot-call.log`; do not paste it anywhere before the checks below.

## 5. Afterwards (offline)

```powershell
Select-String -Path pilot-call.log -Pattern '\+?\d{10,15}'          # expect no output: numbers are masked
Select-String -Path pilot-call.log -Pattern 'voice_live_turn_summary|reconnect|voice_agent_cut|pilot_'
Get-Content pilot-call.log | python scripts\voice_latency_report.py  # one call is not a distribution
```

`voice_live_turn_summary` (one line per call, numbers only) reports `extra_provider_requests`, `judge_timeouts`, `judge_errors`,
`judge_late`, `cuts` by reason, `filler_ignored`, and the judge token counts. Then read the database counts (row counts only):
exactly one new conversation, the old one untouched, recording none.

## 6. Roll back

| What | How |
|---|---|
| The call settings | Delete these from `.env.pilot`: `VOICE_RECONNECT_RECOVERY`, `PILOT_MAX_OUTBOUND_CALLS`, `VOICE_OUTBOUND_TIME_LIMIT_SECONDS`, `VOICE_OUTBOUND_RING_TIMEOUT_SECONDS`, `VOICE_BRAND_NAME`; set `MAX_CONCURRENT_CALLS` back to 2 |
| Meta / Brevo / Twilio | Remove the leading `# DISABLED for the Phase 8G ...: ` tag from those lines (values were kept) |
| The allowlist | Re-add the two provider numbers only if you need them as destinations (you do not) |
| The business content | Greeting `PUT` with `null`, `DELETE /admin/scripts/{id}`, `DELETE /conversation-engine/knowledge/{id}` x7, or drop the Neon project |
| Provider settings | Nothing was changed at Plivo, Twilio, Meta or Brevo, so there is nothing to restore |
| Code | The Phase 8H code change is counters only (`LiveTurnStats`); nothing is committed; `git diff` shows it |

## Webhook signatures: what to expect, and the one decision

In pilot mode `/voice/answer`, `/voice/campaign-hangup` and `/voice/recording-callback` reject (HTTP 403, empty body) any request that
does not carry a valid Plivo V3 signature, and `/voice/stream` closes (code 1008) any stream that does not carry the token our own answer
webhook put in its URL. The algorithm is pinned to Plivo's SDK in the tests, but **only a real Plivo request proves it on this
tunnel.** If a genuine request were rejected, the call would end at answer, and the attempt would still count against the one-call limit.

- Look at the log right after the call is answered: `voice_webhook_rejected` with `reason` and `host_matches_public_base` (a yes/no) is the whole diagnosis. `host_matches_public_base: false` means `PUBLIC_BASE_URL` is not the address Plivo was given.
- **Enforcement stays on for the whole call** (Phase 8I decision). Do not set `warn` or `off` to rescue a call: a rejection of a genuine request is a stop condition to investigate, not a reason to disable authentication.
- **Replay control for the one outbound call (Phase 8K).** The answer to a call we dial is authorised by a random single-use ticket minted when we dial (carried in the signed answer URL, stored in Redis for 180 s, bound to org and destination, taken exactly once). A repeat, a capture or any request made before today finds no ticket and gets 403, so this needs no remembered-nonce epoch. The nonce ledger stays uninitialised on purpose, which keeps every route that still depends on it (inbound answers, status and recording callbacks) closed. The stream is held by one owner at a time and needs one small state record: after you approve it, run `python scripts\pilot_security_initialize.py --streams-only --confirm-all-old-stream-tokens-expired --confirm-all-pilot-workers-stopped`, then `scripts\pilot_security_status.py` must show `outbound_call_ready: true`. Until it does, enforcement refuses the stream and the call is wasted. If Plivo ever retried an answer request after the first was consumed, the retry is refused (fail closed). Details: [pilot-8k-voice-readiness.md](pilot-8k-voice-readiness.md).
- Per-org Plivo accounts are checked with the token of the org named in the URL (`org_id`); a request with no `org_id` is checked with `PLIVO_AUTH_TOKEN`.

## Call script (about 110 s: Plivo ends the call at 120 s, and an abrupt end then is expected, not a defect)

Smart turn-taking and enhanced dialogue are **off**, so this exercises the plain script and the Realtime model's own memory of the call.
Say everything in natural Hinglish, short sentences, like a busy person. Use an invented name and no real details.
An earlier test booked **11:00 IST on Thursday 22 Oct 2026**; while it is still in the future, any booking within 30 minutes of it is refused, and
once that time has passed it no longer matters. Use "kal shaam 4 baje" (16:00) as below, which is clear of it either way; if "kal" would be a
Sunday, say "Monday shaam 4 baje". The org's demo window is Monday to Saturday, 10:00 to 18:00 IST.

| Time | Tester says | Expect | Check in |
|---|---|---|---|
| 0:00 | *(answers)* "Hello?" | One short greeting that **says it is an AI assistant**, names Veerox | listening |
| 0:12 | "Haan... hmm... ek minute, main soch raha hoon..." then **3 seconds of silence** | Does not talk over the pause. At most a very short "ji, aap araam se" | listening; no `voice_agent_cut` |
| 0:25 | "Mera budget shayad das hazaar hai... nahi nahi, paanch hazaar." | Acknowledges the corrected figure briefly; **states no price**, discount or plan | listening |
| 0:40 | "Achha, maine budget kitna bola tha?" | Says **paanch hazaar** (the correction), not das | listening: continuity |
| 0:50 | While it is explaining the demo, cut in: "Ruko, ek minute." | Stops promptly, no words of the old answer afterwards, then waits | `voice_agent_cut`; `voice_live_turn_summary.cuts` |
| 1:05 | "Abhi busy hoon, kal baat kar sakte ho?" | Asks **what time** suits; does not choose one | listening |
| 1:15 | "Kal shaam 4 baje." | Repeats day and time back and waits; after your "haan, theek hai" it says it is noted, not before | one callback row; the 22 Oct 11:00 appointment untouched |
| 1:35 | "Mujhe kisi insaan se baat karni hai." | Honest: the **team will follow up**; does **not** say a person is on the line or being connected now (no one is notified in this pilot) | listening; a handoff row, no WhatsApp |
| 1:50 | "Theek hai, bye." | One short goodbye; hang up | listening |

If time runs short, keep the greeting, the budget correction, the callback and the human request, and drop the pause and the interruption.
A successful callback adds a callback row only (the callback worker is off, so nothing will dial it).
If there is a gap, ask once: "Kya aap robot ho?" -> it should say plainly that it is an AI assistant.

## Rotating the Neon and Upstash credentials (console steps; nothing in this repo does it for you)

You said these are test credentials, but the current ones were pasted into a chat session, so they exist in that session's transcript
file on this machine and in the model provider's logs. Rotating them is the fix. Menu names change; follow the console.

1. **Neon** (console.neon.tech): project -> *Branches* -> the branch -> *Roles & Databases* -> the role -> **Reset password**. Copy the new
   connection string (*Dashboard -> Connect*) straight into `.env.pilot` as `DATABASE_URL` (the repo's asyncpg form: `postgresql+asyncpg://...`).
   The old password stops working at once. If the pilot data is disposable, deleting the project and creating a new one is the other route;
   then run the migrations, `scripts/pilot_setup_org.py` and `scripts/pilot_apply_org_config.py` again.
2. **Upstash** (console.upstash.com): *Redis* -> the database -> *Details* -> **Reset Password** (or create a new database). Copy the new
   `rediss://default:<password>@<host>:6379` URL into `.env.pilot` as `REDIS_URL`.
3. Type the new values into `.env.pilot` yourself, not into a chat. Then run `python scripts\pilot_preflight.py .env.pilot --connect`.
4. Tell me when it is done and I will re-check that the file no longer matches the transcript (booleans only), without reading or showing the values.
5. Optional: delete the old session transcript file under your `.claude\projects` folder for this project.
