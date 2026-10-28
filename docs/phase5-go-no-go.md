# Phase 5: GO / NO-GO for ONE supervised outbound staging call (28 Oct 2026)

**Verdict: NO-GO.** Three things stand between this and a call: your approval of the actions in section 4, a spending ceiling above Rs 0, and a pilot process running on its own tunnel. Nothing has been applied, started, restarted, committed, deployed or paid for.
All checks were read-only (SELECT counts, Redis read commands, free GET lookups, offline tests in a scratch copy). No secret, number or message content was printed.

## Your decisions, as applied
1. The Redis `human_handoff_queue` entry is **preserved**, not consumed or cleared.
2. The pilot database conversations, messages, leads and appointment are **preserved** and untouched.
3. The running development stack is **not stopped or modified**; staging gets its own port and tunnel.
4. The only Plivo number is the production number: it is used **only as outbound caller ID**; nothing about its inbound webhook, application or assignment changes.
5. Outbound cap stays **1**; the cap is not reset to get around the limit.
6. Spending ceiling stays **Rs 0** until you approve a paid test.
7. **Every experimental flag stays off** for the first call (baseline configuration).

## 1. Verified facts
| # | Fact | How it was verified |
|---|---|---|
| 1 | The staging process reads only `.env.pilot` when `VEEROX_ENV_FILE` is set; nothing is inherited from `.env`; no shell variable overrides it | Effective settings instantiated under the variable; offline preflight "loads ONLY the pilot file" passes |
| 2 | Pilot Postgres is its own database (declared pilot name), 1 org, different host from the main `.env` | Read-only connection preflight |
| 3 | Pilot Redis is a different instance from the main `.env` and from production's | Host comparison; read-only PING/SCAN |
| 4 | The handoff entry is **stale**: written 21 Oct 2026 (7 days old), voice, urgency medium, from the **allowlisted tester's own number**, in the pilot org; its conversation and user exist in the pilot database. Nothing in the code ever removes entries from that list (it is only pushed to and read) | `LINDEX 0` (non-destructive), metadata and booleans only; code read |
| 5 | The pilot database leftovers are all **voice** and all dated **21-22 Oct 2026** (3 conversations, 56 messages, 2 leads of intent outreach and human_support, 1 appointment): consistent with the earlier controlled pilot calls, not with customer traffic. Origin is still not proven | Date and channel aggregates only |
| 6 | The startup seed **changes nothing** in the preserved data: org, owner, membership, admin-token hash and calling number are all already stored and match | Read-only comparison of the seed's inputs against the database |
| 7 | A staging call's `answer_url` is `PUBLIC_BASE_URL/voice/answer?org_id=...`, and the answer XML contains exactly **one** URL: the `wss://` media stream built from `PUBLIC_BASE_URL`. Recording callbacks (off in pilot) would also use it | Code read; 5 new offline tests (section 3) |
| 8 | Provider-side limits go out with the call: `time_limit` 120 s, `ring_timeout` 25 s | Code read; offline test asserts the request payload |
| 9 | Starting the pilot API contacts no provider: no answer-URL registration (flag off and `provider_config_changes_blocked`), no Meta webhook registration, no dialer or dispatchers | Code read of startup and the two config-changing functions |
| 10 | Outbound calls, SMS, WhatsApp and e-mail are allowlist-checked; the Meta, Twilio and Brevo credentials are unset in the pilot file, so those channels cannot send | Code read; effective settings |
| 11 | Voice, model and keys: ElevenLabs `eleven_turbo_v2_5` with a voice that exists; `gpt-realtime-2.1`, `gpt-live-transcribe`, `gpt-4o-mini`, `whisper-1` available to the key | Free GET lookups |
| 12 | A separate port (8003) is free and `cloudflared` is installed, so a second tunnel does not need the ngrok agent that serves the dev stack | Port and tool check |
| 13 | A hangup event without a campaign id triggers no workflow: `/voice/campaign-hangup` is signature-guarded and returns 204 doing nothing | Code read; offline test asserts `handle_call_ended` is never called |
| 14 | Production is unchanged: HEAD `0b3bdc1`, nothing pushed or committed, no stash; only free read-only calls used the shared credentials | git |

## 2. Unresolved risks
1. **The hangup callback destination (requirement 5): NOT verified today.** Plivo's documentation does not say where the hangup notification goes when `hangup_url` is omitted; it ties outbound hangup data to the `hangup_url` set in the request. The dashboard dial sends none. One observation from the earlier pilot call (a rejected request on staging's `/voice/answer` about 49 s after answer) suggests it went to the staging `answer_url`, but one observation does not verify it. **Under your rule this is a NO-GO until the destination is named explicitly.** The proposed patch (section 4, action B) does that; it is built and tested in a scratch copy only.
2. **Caller ID is the production number.** If the tester or anyone calls it back, production's AI answers (inbound is unchanged and still points to production). The tester must not call that number back.
3. **Shared Plivo account:** the staging call counts against the same account, balance and concurrent-call capacity as production.
4. **No hard monetary gate** exists. Controls are time and count only: provider limit 120 s, app watchdog 100 s, cap 1, tester hangs up by 90 s. The 120 s limit is documented and coded but has not been observed working. The cap is per process and a restart resets it (not to be used to bypass it).
5. **Hard-kill gap:** if the pilot process is killed abruptly the app watchdog and stream-end hangup do not run; only Plivo's 120 s limit ends the call.
6. **Shared secrets and the public tunnel (accepted by you):** the staging API is reachable from the internet through the tunnel and its admin token is the production token. A leak of the tunnel URL plus that token would expose the staging admin API. Keep the tunnel up only during the call.
7. **Redis preflight stays FAIL** ("dedicated, empty instance") by your decision to preserve the entry; the other key is the required security record.
8. Quick tunnels (cloudflared) carry the call audio through a third party and change URL on every start; the URL must be set in `.env.pilot` before the API starts.
9. The pilot rows' origin is probable (earlier pilot calls) but unproven, and the first call will append to them (append-only: conversation, messages, possibly a lead).

## 3. Offline evidence for requirements 4, 5 and 8 (scratch copy, real tree untouched)
`docs/phase5-pilot-routing.patch` (176 lines) holds a change to two files plus 5 new tests. In a scratch copy of the API package: the 5 new tests pass; 6 neighbouring test files give the **identical** result with and without the patch (the same 11 failures in both, caused only by the scratch copy lacking `.env.example`, `.gitignore` and encryption keys). The tests assert that, in pilot mode, the Plivo request carries only staging hosts in `answer_url` and `hangup_url`, the media stream URL is staging, `time_limit` and `ring_timeout` are sent, the hangup URL is `None` outside pilot mode (production requests unchanged), and a plain call's hangup event runs no campaign workflow. They prove what **our code sends**; they cannot prove Plivo's behaviour, which is why the destination is made explicit instead of assumed.

## 4. Actions requiring your approval (none taken)
| Action | What it does | Touches |
|---|---|---|
| A. Choose a spending ceiling above Rs 0 | The existing checklist's planning bound for a 90 s call is about Rs 30 (not a measured bill); it proposed Rs 100 for one call | nothing yet |
| B. Apply `docs/phase5-pilot-routing.patch` (`git apply`) | Pilot mode only: names the hangup URL as `PUBLIC_BASE_URL/voice/campaign-hangup`; adds the tests. Outside pilot mode the Plivo request is unchanged. **Note:** your dev server runs with `--reload`, so changing `.py` files makes it reload itself (no behaviour change in dev mode) | 2 source files, 1 test file |
| C. Edit `.env.pilot` (back it up first) | Section 5 | staging config only |
| D. Start `cloudflared` on port 8003, then the pilot API on 127.0.0.1:8003 (no `--reload`) | The staging process | new processes; the dev stack is untouched |
| E. Run the offline and `--connect` preflights again with the new URL | Expect 0 FAIL except the known Redis item | read-only |
| F. One supervised outbound call to the allowlisted handset | The test itself | Plivo account spend, pilot DB append |

## 5. Exact staging configuration for the one call (baseline, every experimental flag off)
Back up first: copy `.env.pilot` to `.env.pilot.before-phase5`. Changes to `.env.pilot`:
```
PUBLIC_BASE_URL=<the https URL printed by cloudflared in step D>     # replaces the shared ngrok URL
VOICE_RECONNECT_RECOVERY=false          # the only experimental flag currently on
# safeguards (not experimental), proposed:
VOICE_HANGUP_ON_STREAM_END=true         # a dropped stream also ends the Plivo leg (keepCallAlive otherwise leaves it open)
VOICE_MAX_CALL_SECONDS=100              # app watchdog; Plivo's 120 s limit stays the backstop
# unchanged: ENVIRONMENT=pilot, PILOT_MAX_OUTBOUND_CALLS=1, PILOT_ALLOWED_NUMBERS (1 entry), PLIVO_STARTUP_REGISTRATION=false,
#   PILOT_WORKERS_ENABLED=false, LOG_TRANSCRIPTS=false, LOG_PRIVACY_ENABLED=true, VOICE_OUTBOUND_TIME_LIMIT_SECONDS=120,
#   VOICE_OUTBOUND_RING_TIMEOUT_SECONDS=25, VOICE_TTS_PROVIDER=elevenlabs, ELEVENLABS_MODEL_ID=eleven_turbo_v2_5, the same voice,
#   VOICE_LIVE_TURN_DETECTION=true, VOICE_VAD_SILENCE_MS unset (1100)
# explicitly off (code defaults, nothing to add):
#   VOICE_SCRIPT_AUTHORITY VOICE_EXACT_LINES VOICE_IF_TRIGGERS VOICE_HUMAN_STYLE VOICE_CONVERSATION_MEMORY
#   VOICE_OPENER_VARIETY VOICE_TTS_PHRASE_FLUSH VOICE_ADAPTIVE_ENDPOINTING
```
Launch (PowerShell, in this order, in new terminals; the dev terminals stay as they are):
```
# 1  tunnel for the new port only
cloudflared tunnel --url http://localhost:8003
# 2  staging API: pilot file only, local interface only, no reload
$env:VEEROX_ENV_FILE = (Resolve-Path .env.pilot).Path
.\.venv\Scripts\python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8003
# 3  preflights
python scripts\pilot_preflight.py .env.pilot
python scripts\pilot_preflight.py .env.pilot --connect
# 4  confirm the flags the process really runs with
GET http://127.0.0.1:8003/diag/voice-config      (header X-Admin-Token)
# 5  the call: from the staging API, never from the dev dashboard
POST http://127.0.0.1:8003/admin/outbound/call   {"to_phone": "<the allowlisted handset>"}   (header X-Admin-Token)
```
The first call uses the **existing default pilot script unchanged**, so no script row is added and no database record is touched before the call.

Tester script (about 80 s, baseline): greet in Hinglish; ask the price; pause 1.5 s mid-sentence once; correct yourself once ("nahi, kal nahi, parso"); interrupt a long answer with a short question once; say "okay" once while the agent talks; say goodbye and hang up by 90 s. Do not call the caller-ID number back.
After the call: save the API log; verify with `python scripts\voice_postcall_verify.py S03 --logs api.log --call-uuid <uuid>`; check Plivo's call record for terminal status and where the hangup event was sent. This single baseline call shows the pipeline and the measurements work; it cannot show any improvement.

## 6. Stop and rollback
Hang up on the handset; the 120 s Plivo limit bounds it; close the staging terminal (the stream-end hangup closes the leg) and the cloudflared terminal (the public URL dies with it). To undo configuration: restore `.env.pilot.before-phase5`, `git apply -R docs/phase5-pilot-routing.patch` if it was applied. Pilot database rows and the Redis entry are append-only for this call and are not removed.

## 7. GO / NO-GO checklist
| # | Item | State |
|---|---|---|
| 1 | Env pinned to the pilot file | VERIFIED |
| 2 | Postgres and Redis are the pilot resources, separate from production's | VERIFIED |
| 3 | Redis entry and pilot database records preserved; startup seed is a no-op on them | VERIFIED |
| 4 | Answer URL and media WebSocket URL point only at staging | VERIFIED (code + tests) once `PUBLIC_BASE_URL` is the new tunnel |
| 5 | **Hangup callback destination** | **NOT VERIFIED, NO-GO** until action B is approved and applied (then verified by construction + tests) |
| 6 | No staging request can trigger a production workflow | VERIFIED for our code (provider-config writers blocked, hangup route inert, no other channels configured); the production inbound line is unchanged |
| 7 | Cap 1, allowlist 1, workers off | VERIFIED |
| 8 | Experimental flags off | VERIFIED defaults; `VOICE_RECONNECT_RECOVERY` must be turned off (action C) |
| 9 | Separate port and tunnel | AVAILABLE (8003, cloudflared); not started |
| 10 | Dev stack untouched | VERIFIED; applying action B reloads it |
| 11 | Spending ceiling | **Rs 0, NO-GO** until you name one (action A) |
| 12 | Caller ID is the production number | RISK, needs your acknowledgement |
| 13 | 120 s limit observed to work | UNPROVEN until the call; hard-kill relies on it alone |
| 14 | Production unchanged, no commit, no deploy | VERIFIED |
