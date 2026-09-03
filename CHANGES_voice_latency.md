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

---

# Outbound-call greeting latency — 2026-09-03 (second change)

## What changed
`OpenAI Realtime` warm-up (`start_precall_connect`) now also fires the moment
an outbound call is *placed*, not only once Plivo/Twilio reports it as
*answered*.

- `apps/api/channels/voice/realtime_bridge.py` — `start_precall_connect` gained
  an idempotency guard: if a pending connection already exists for a given
  call id, a second call is a no-op instead of clobbering it with a duplicate
  OpenAI connection.
- `apps/api/routers/admin.py` — `outbound_call` (single dashboard-placed call)
  now calls `start_precall_connect` right after `initiate_call()` returns,
  keyed by the provider's own call-request id (Plivo `request_uuid` / Twilio
  `sid`).
- `apps/api/workers/campaign_dialer.py` — `_dial_one` does the same for
  campaign-placed calls. `_claim_targets` now also returns each target's
  `org_id` (it was already being fetched from the join, just not returned)
  so `_dial_one` has it to pass through.

## Why
Reported symptom: "when I call the AI it responds instantly, but when the AI
calls me, the greeting takes longer to start." Root cause isn't AI speed —
it's *when* our backend even learns the call started:

- **Inbound** (you call the AI): our own server auto-answers programmatically
  — near-zero delay — so `start_precall_connect` (fired from `answer()`) has
  already had time to fully warm up OpenAI by the time the audio path opens.
- **Outbound** (AI calls you): a real phone rings, a real person answers, and
  their carrier has to signal "answered" back through the PSTN before our
  `answer()` webhook fires at all — genuine telecom latency outside our code.
  Previously, OpenAI warm-up only *started* at that point, so the ring
  duration was wasted time instead of prep time.

This change starts the OpenAI connection at dial-time instead, so by the time
a real person actually answers, the session is (usually) already warm and the
greeting can fire immediately — same effect as the inbound path already gets.

## Tradeoff / risk
None expected in the normal case — it's additive and fails closed:
- **Twilio**: the `sid` returned when placing the call is guaranteed to be the
  same `CallSid` the answer webhook reports later, so this reliably matches.
- **Plivo**: `request_uuid` is NOT officially documented as always equal to
  the `CallUUID` reported later. If they don't match, the idempotency guard
  means nothing breaks — the dial-time connection just goes unclaimed and
  self-expires after 20s (`_PRECALL_TIMEOUT_SECS`), and `answer()`'s own call
  starts a fresh connection exactly as it did before this change. Worst case
  is zero improvement for that call, never a regression.
- One extra OpenAI Realtime connection gets opened per outbound call attempt
  even if it's never claimed (self-expires unclaimed on a Plivo id mismatch)
  — a minor, bounded cost, not a correctness risk.

## Post-deploy review (2026-09-03) — checked for campaign-side issues
Ran the full test suite (278 tests) and traced every other caller of
`_claim_targets`/`_dial_one`/`start_precall_connect` — nothing else in the
codebase depended on the old tuple shape or signature; no mechanical
breakage found. Two real behavioral tradeoffs surfaced on closer read,
though, both **decided: keep as-is for now**, revisit only if actual
Render/OpenAI usage shows a problem:

1. **Every dial attempt now opens a real OpenAI connection, not just
   answered ones.** Before this change, `answer()` (and the OpenAI session
   it triggers) only ever fired for calls that were actually picked up. Now
   `start_precall_connect` fires right after `initiate_call()` succeeds, so
   busy/no-answer/voicemail/failed campaign attempts also open a session
   (self-closes after 20s, no leak, but it's still connection + instruction-
   text overhead that didn't exist before for calls nobody answers). Cold-
   calling campaigns commonly have 30-60%+ no-answer rates, so this is a
   real increase in OpenAI Realtime connection volume on the campaign
   dialer specifically, not just a latency tweak.
2. **The 20s self-expiry (`_PRECALL_TIMEOUT_SECS`) can undercut the benefit
   on slow answers.** A phone ringing past 20s before pickup (common —
   carriers often ring 20-30s+ before voicemail) means the pre-warmed
   connection auto-closes and gets discarded before the person answers;
   `answer()` then falls back to a fresh connect, same as pre-change
   behavior — zero benefit for that call, silently.

Both fail closed to prior behavior (no regression, no leak) — the cost is
usage volume, not correctness. If OpenAI usage/cost jumps after this
deploys, this is where to look first; the two considered mitigations were
(a) scope dial-time warm-up to admin single calls only, dropping it from
`campaign_dialer.py`, or (b) raise `_PRECALL_TIMEOUT_SECS` to catch more
slow-answer calls at the cost of more connection volume — neither applied.

## Verify live
Watch Render logs for `voice_stream_connected` / `openai_realtime_connected`
timing on an actual outbound (admin-placed or campaign) call, and listen for
whether the greeting starts faster than before. If Plivo's `request_uuid`
turns out to never match, this becomes a no-op for Plivo calls specifically
(Twilio still benefits) — worth confirming which provider you're actually
dialing from before assuming this fixed it.

## Rollout
Code-only change, no env var involved — picks up on Render's next deploy from
a push to `main`.
