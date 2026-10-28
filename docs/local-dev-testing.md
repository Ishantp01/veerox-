# Testing Veerox locally from the website (development stack)

The website at http://localhost:3001 calls the development API at http://localhost:8002 (`apps/web/.env.local`). Plivo reaches that API through the ngrok tunnel to port 8002. Settings come from the main `.env` (`ENVIRONMENT=dev`). The pilot staging stack (port 8003, `.env.pilot`) is separate and the website does not talk to it.

## What was changed for safe local testing (main `.env`; backup: `.env.before-dev-voice-fix.local`, git-ignored)
| Setting | Value | Why |
|---|---|---|
| `ELEVENLABS_VOICE_ID` | the validated Armaan voice | the previous voice id did not exist, so the agent could not speak |
| `VOICE_PREFLIGHT_VOICE_CHECK` | true | a dial is refused (503, nothing written) if ElevenLabs rejects the voice |
| `VOICE_DIAL_ALLOWLIST` | the one tester number | calls, SMS and WhatsApp from this stack, including the background campaign dialer, can reach only that number |
| `VOICE_MAX_ACTIVE_STREAMS` | 1 | one call at a time |
| `VOICE_OUTBOUND_TIME_LIMIT_SECONDS` / `RING_TIMEOUT` | 300 / 30 | Plivo ends a call after 5 minutes and stops ringing after 30 s, even if this process dies |
| `VOICE_MAX_CALL_SECONDS` | 240 | the app hangs up at 4 minutes |
| `VOICE_HANGUP_ON_STREAM_END` | true | a dropped media stream also ends the Plivo leg |
| `VOICE_PIPELINE_DIAG` | true | one log line per agent turn and per call (counts and short error codes only) |

## Before every session (no call involved)
1. Confirm both servers are running: the API on 8002 (`uvicorn apps.api.main:app --reload --port 8002`), the website on 3001, and the ngrok tunnel (`ngrok http 8002`).
2. Run `python scripts\voice_preflight.py`. It must end with `RESULT: ready`. An invalid voice, a missing OpenAI model or a tunnel that is down is reported here instead of as a silent call.
3. Optionally `GET http://localhost:8002/diag/voice-config` (header `X-Admin-Token`) to see the flags and warnings the running process loaded.

## The test call
1. Open the website, log in, open the Dial page and dial the allowlisted tester number. Any other number is refused with "That number is not on the allowlist; no call was placed."
2. Watch the API terminal. A healthy turn logs `voice_pipeline_turn` with `silent_turn: false` and `tts_chunks` above 0. A failed synthesis logs `voice_tts_failed` at error level and is not saved as something the agent said. `voice_pipeline_call_summary` at the end of the call names the first failing stage.
3. Hang up from the phone. Expected: the agent greets first on an outbound call.

## Stop / roll back
Hang up; Plivo's limit ends a call at 5 minutes regardless. To undo the configuration: copy `.env.before-dev-voice-fix.local` over `.env` (the API reloads on the next code change or restart).

## Known limits
- The dev stack uses `eleven_multilingual_v2`, which starts speaking about 0.5 s later than flash/turbo; change `ELEVENLABS_MODEL_ID` if latency matters more than quality.
- Calls placed from this stack are recorded by the provider (the dev database's org policy), and the dev API listens on all network interfaces, not only loopback.
- The allowlist covers calls, SMS and WhatsApp. E-mail (Brevo) from the dev stack is not covered.
