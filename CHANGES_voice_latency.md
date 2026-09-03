# Voice call latency change — 2026-09-03

## What changed
`apps/api/channels/voice/realtime_bridge.py` — `_session_update_event()`, the
`turn_detection` block sent to OpenAI Realtime on every call:

```
"eagerness": "low"   →   "eagerness": "medium"
```

(`type` stays `semantic_vad` — unchanged.)

## Why
Callers reported the AI taking ~2-3 sec to start replying on voice calls
(WhatsApp text replies feel much faster by comparison). One real cause:
`semantic_vad` with `eagerness: "low"` deliberately waits longer after the
caller stops talking before deciding they're actually finished, so a quick
backchannel word ("ok", "haan", "theek hai") doesn't get mistaken for the
caller taking the turn and cause a false interruption. That safety margin
was adding real, perceived latency to every single reply.

## Tradeoff (read before reverting or escalating further)
`"medium"` trims that wait, so replies come faster — but it also raises the
chance the AI starts responding while the caller is still mid-sentence
(e.g. someone pausing briefly to think before finishing a thought). This is
a genuine UX tradeoff, not a bug: faster turn-taking vs. fewer false
interruptions. If callers start reporting the AI talking over them, that's
this change — the fix is dialing eagerness back toward `"low"`, not adding
other latency workarounds.

## Not changed (still on the table if medium isn't enough)
- `OPENAI_REALTIME_MODEL` is still `gpt-realtime-2.1` (full model) in `.env`.
  Switching to `gpt-realtime-2.1-mini` was discussed as a further speed
  lever but is untested live — do a real test call before trusting it.
- Tool-call round trips (checking availability, creating leads, etc. mid-call)
  still add 2-5s on turns that need them — inherent to needing that data,
  not something this change touches.

## Rollout
Code-only change, no env var involved — picks up on Render's next deploy
from a push to `main` (~2-3 min rebuild, see deployment notes in memory).
