# Voice call drop + language-switch fixes (2026-09-23)

## Symptom 1: call disconnects completely when the caller asks rapid questions

Reported: asking questions rapidly (barging in repeatedly) sometimes ends the
call entirely, not just cuts the agent's speech.

### Investigation

Pulled prod logs (EC2, `docker compose logs api`) for two calls that dropped
(`7ff0717f...`, `1e087579...`). Both show the same pattern in the last few
events before the drop:

```
voice_caller_spoke_over_response
voice_agent_cut        reason=live_fallback  still_generating=true
openai_ws_closed        call_duration_s=47.4
```

The OpenAI Realtime WebSocket itself was closing mid-call (not a Plivo/
Twilio-side hangup), and `realtime_bridge.py`'s pump loop treated *any* end
of the OpenAI leg as the end of the whole call — it cancelled the caller's
own WebSocket task and tore the call down. Both drops correlated with a high
rate of caller interruptions (10 barge-ins in a 47s call).

The exact OpenAI-side close reason wasn't knowable from the old logs — only
`call_duration_s` was captured, not the WebSocket close code/reason.

### Fixes (`apps/api/channels/voice/realtime_bridge.py`, `adapter.py`)

1. **Better diagnostics**: `openai_ws_closed` now logs `close_code` /
   `close_reason` from the `websockets.ConnectionClosed` exception, so the
   next drop is diagnosable instead of a guess.
2. **Auto-reconnect**: only the caller's own leg (`call_task`) is allowed to
   end the call now. If the OpenAI leg dies on its own, the bridge:
   - cancels any background tasks still holding the old (dead) OpenAI socket
     (`voice_adapter.cancel_pending_tasks`, new helper in `adapter.py`),
   - opens a fresh OpenAI Realtime session with the same instructions,
   - tells the agent (in Hindi) to briefly acknowledge the glitch and ask
     the caller to repeat themselves (a fresh session has no conversation
     history),
   - and the call continues — the caller is never dropped.
   - Bounded to 8 reconnect attempts with backoff, as a safety net in case
     OpenAI itself is down (so a dead call doesn't hold the line open
     forever billing nothing).

## Symptom 2: agent switches to English mid-call on its own

Reported: after the Hindi greeting, the agent would start replying in
English even though the caller kept speaking Hindi, and only explicit
"English mein baat karo"-style requests should trigger a switch.

### Root cause

`adapter.py` had a one-shot "language hint" (`_apply_language_hint`) that ran
language detection on the caller's *very first* transcript of the call and
locked the session to whatever it detected, unasked. A bare first utterance
like "hello" or "haan" is enough for the detector to (correctly, for that
one word) call it English — and that then overrode the Hindi greeting's
language for the rest of the call.

### Fix

Removed the automatic hint entirely (`_apply_language_hint` and the
`language_hint_sent` state field deleted). Language now only changes on an
**explicit** request from the caller, matched by the existing
`_LANGUAGE_SWITCH_CUE_RE` → `_handle_language_switch_request` path (e.g.
"English mein baat karo", "switch to Tamil", "हिंदी में बात करो"). The agent
stays on the greeting's language (Hindi) by default until asked to change.

## Status

- Reconnect + logging fix: committed (`1141bd4 added calling end logic`).
- Language auto-switch removal: pending commit as of this writing.
- Not yet deployed/verified against a real live call — recommend testing:
  1. Rapid interruption call → confirm call survives and the next
     `openai_ws_closed` log (if any) shows `close_code`/`close_reason`.
  2. A call where the caller's first word is ambiguous ("hello") → confirm
     the agent stays in Hindi until an explicit switch request.
