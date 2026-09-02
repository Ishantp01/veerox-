# Latency reduction — what changed

Date: 2026-09-02

Three separate pieces of work, done in this order. Parts 1 and 2 are live in
production (WhatsApp text channel). Part 3 (voice channel) is written and
tested but **not deployed yet** — needs an explicit commit + push before it
has any real effect (see its own status note below).

Diagnosis throughout used the existing (pre-existing, not created this
session) debug endpoint `GET /diag/latency` in `apps/api/routers/diag.py`
(needs `X-Admin-Token` header, token in `.env`'s `ADMIN_TOKEN`) — returns
dependency timings plus the last 10 real WhatsApp turn timings recorded into
Redis key `veerox:diag:wa_timings`.

---

## 1. Redis region/tier fix (live in production)

### What was wrong

The Upstash Redis database (`social-owl-134556.upstash.io`) was on the
**Global** tier. Global-tier databases replicate every write across all
read-regions in the group before acknowledging — a real cost even though
Render (Ohio) and the database's primary region (`us-east-1`, N. Virginia)
are geographically close on AWS's backbone (normally ~10-15ms).

Measured via repeated `/diag/latency` calls: `redis_ping_ms` and
`redis_set_ms` sat consistently at **~196ms**, against a documented healthy
baseline of ~14ms. Repeating the probe several times, seconds apart, ruled
out a one-off cold-start cost — it was a steady-state regression.

### Why it actually mattered to customers

Traced where Redis sits in the real WhatsApp turn path:
- `_claim_message_id` (idempotency `SETNX`) — [apps/api/channels/whatsapp/adapter.py:174-179](apps/api/channels/whatsapp/adapter.py#L174-L179), called **before** the turn's own timer starts, so its cost was invisible in the turn-timing breakdown but still fully customer-facing.
- `_is_kill_switch_active` (Redis `GET`) — [apps/api/core/agent.py:101-105](apps/api/core/agent.py#L101-L105), called inside `handle_turn`, counted inside `agent_ms`.

Both blocking, both on every single turn. At ~196ms each that's ~390ms of
pure Redis tax per turn, versus ~28ms at the documented baseline.

### The fix

Created a new Upstash Redis database on the **Regional** (non-Global) tier,
pinned to `us-east-1` — `classic-heron-79420.upstash.io` — replacing the old
Global-tier `social-owl-134556.upstash.io`.

**Changed:**
- `.env` — `REDIS_URL` updated to the new connection string (local dev).
- Render dashboard — `REDIS_URL` environment variable updated to the same
  new connection string, redeployed. (Done by the user directly — no
  dashboard/API access from here.)

No data migration was needed: everything Redis held was disposable
(idempotency keys, 24h TTL; the diag timings list; the kill-switch flag,
which was checked as empty before cutover).

### Result (confirmed via `/diag/latency`, run repeatedly after cutover)

| Metric | Before (Global tier) | After (Regional, us-east-1) |
| --- | --- | --- |
| `redis_ping_ms` | ~196ms | ~2ms |
| `redis_set_ms` | ~197ms | ~2ms |

Better than the original ~14ms baseline, not just back to it.

### Left over, not yet done

The old `social-owl-134556` Global-tier database still exists and is
unreferenced. Safe to delete once things have been stable for a while — not
done yet, flagged to the user as a follow-up.

---

## 2. Parallel tool-call dispatch (live in production)

### What was found

In `AgentCore.handle_turn` ([apps/api/core/agent.py](apps/api/core/agent.py)),
when the model returns more than one tool call in a single turn (e.g.
`book_appointment` + `send_whatsapp_message` together), they were dispatched
one at a time in a sequential `for` loop, all sharing one DB session — so
each extra tool call in the same turn added a full extra round trip.

### Validated it actually happens before building it

Ran a local, throwaway probe (not committed to the repo) against the real
`gpt-4o-mini` model, the real system prompt, and the real `TOOL_DEFINITIONS`,
with 7 realistic multi-intent WhatsApp-style messages. Result: **1 of 7**
produced a genuine 2-tool-call turn (`book_appointment` +
`send_whatsapp_message`, two *different* tools) — enough signal to justify
building this rather than optimizing an edge case that never fires.

### The change

In `apps/api/core/agent.py`:
- New helper `_dispatch_tool_isolated(...)` — runs one tool call on its own
  fresh `AsyncSessionLocal()` session instead of the shared one.
- `handle_turn`'s tool-dispatch loop now branches:
  - **>1 tool call in the batch, all distinct tool names** → dispatched
    concurrently via `asyncio.gather`, each on an isolated session.
  - **Single tool call, or duplicate tool names in the same batch** →
    unchanged sequential path, on the shared session, exactly as before.
- Added imports: `asyncio`, `from apps.api.db.session import AsyncSessionLocal`.

### Why the same-tool-name guard exists

`book_appointment` works by read (check for a conflicting slot) → write →
commit ([apps/api/core/tools.py:411-437](apps/api/core/tools.py#L411-L437),
`find_conflicting_appointment`). With one shared session, two *sequential*
calls to it are safe — the second call's read happens after the first's
commit, so it correctly sees the conflict. Running two calls to the *same*
mutating tool concurrently on separate sessions would let both reads happen
before either commits — both could pass the "slot is free" check, i.e. a
double-booking. Distinct tools never touch each other's state, so isolated
sessions are safe for them; same-named calls fall back to the safe
sequential path instead.

### Pre-existing gap noticed, NOT fixed here

`find_conflicting_appointment` is a plain read-then-write check with no
database-level uniqueness/exclusion constraint backing it. That means the
same race already exists today, independent of this change, any time two
*different* customers' conversations happen to book the same slot at nearly
the same moment. Worth a separate follow-up (a Postgres `EXCLUDE` or unique
constraint on the appointment slot) if it matters — not attempted as part of
this work.

### Test added

`apps/api/tests/test_agent_core.py` —
`test_handle_turn_parallel_dispatch_distinct_tools`. Exercises the real
`asyncio.gather` branch with two real tool handlers (`lookup_customer` +
`capture_lead`) against the same in-memory test database used by the rest of
the suite (repoints `agent_module.AsyncSessionLocal` at a session factory
bound to the test's own engine, otherwise the isolated-session branch would
try to reach the real production database during tests). Confirms: both
tools' effects land correctly, results map back to the right
`tool_call_id`, and ordering is preserved for the model's next turn.

Full suite: **275/275 passing** locally after this change.

### Status — live

Committed as `279682f` ("latency reduced"), pushed to `main`, confirmed
matching `origin/main`, Render auto-deployed. Verified the deployed app was
up and responding correctly afterward via `/diag/latency`.

Only helps the subset of turns where the model batches independent tool
calls together — most turns are single-tool or no-tool and are unaffected
by it. In the one real batched turn observed during testing, the model
happened to spread `book_appointment` + `send_whatsapp_message` across two
separate sequential iterations instead of one batch, so it didn't exercise
the new parallel branch that particular time — expected model
non-determinism, not a bug (confirmed the branch itself is correct via the
dedicated unit test, which forces the batched case directly).

---

## 3. Voice channel: call-start delay + turn-taking delay (written, tested, NOT deployed)

Client feedback (via WhatsApp, forwarded by the user): *"initially it takes
time to respond whenever we call... takes time to respond."* This is a
completely different pipeline from parts 1-2 — OpenAI Realtime API over a
persistent WebSocket + Plivo/Twilio telephony, not the stateless
`gpt-4o-mini` chat-completions path. Two distinct delays turned out to be
involved.

### 3a. Call-start delay (connect → caller hears the greeting)

**Measured directly** against the real OpenAI Realtime endpoint, using this
org's actual (large) instructions, mirroring exactly what
`realtime_bridge.py` does on a real call: connect → `session.update` →
`response.create` → first `response.audio.delta`. Result: **~2.2-2.3s**
end-to-end on a first pass, breaking down as:
- WebSocket connect to OpenAI: **~1.2-1.3s** (the dominant cost — more than
  half the total)
- `session.update` → `response.created`: near-instant (~0.0003s) — **the
  large system prompt is NOT the bottleneck here**, contrary to the initial
  assumption
- → first audio chunk: ~0.3-0.4s more

**Root cause**: [`voice_stream`](apps/api/channels/voice/realtime_bridge.py#L222)
did everything sequentially — wait for the Plivo/Twilio handshake → resolve
org → open DB conversation → fetch the org's script → *then* open the
WebSocket to OpenAI. The OpenAI connection doesn't depend on any of that
and could start immediately.

**The fix**: kick off the OpenAI WebSocket connection the instant the call
arrives (right after `ws.accept()`), running concurrently with the
Plivo/Twilio handshake read and the DB/org-resolution work, instead of
after it. Awaited once, right before it's actually needed (just before
`session.update`).

- `websockets.connect(...)` returns its own awaitable `Connect` object, not
  a plain coroutine — `asyncio.create_task()` requires an actual coroutine,
  so the connect call is wrapped in a small `async def _connect_to_openai()`
  helper. **Caught this the hard way**: the first version passed
  `websockets.connect(...)` directly to `create_task()`, which is a real
  `TypeError` that would have crashed every single call in production. Only
  caught it by directly re-running the timing test against the real
  endpoint before considering this done — worth remembering next time
  something "obviously correct" touches `websockets.connect`.
- Connection cleanup: the existing code already closed `oai` inside
  `pump_call_to_openai`'s `finally` on the normal path; added an outer
  `finally` as a safety net for the case where something raises before the
  pumps ever start (so a fully-connected-but-unused session, or a
  still-connecting task, doesn't leak).

**Verified the mechanism actually saves time** (3 runs, real OpenAI
endpoint, ~0.35s of simulated concurrent setup work standing in for the
real DB/handshake work): saved 0.48s, 0.65s, and 2.87s respectively over
the sequential version — consistently faster, never worse.

**Important caveat found afterward**: raw OpenAI connect time is highly
variable run-to-run. Measured **~1.2-1.3s** earlier in this session, then
**~3.5-4.2s** consistently a bit later, from the same machine/network, with
no code changes in between — pure network/OpenAI-side variability, outside
anything the app controls. The user test-called locally afterward and saw
**~2.5s total** — *slower* than the original ~2.2-2.3s target, but actually
consistent with the fix working, since a bare unparallelized connect alone
was measuring 3.5-4.2s+ at that same moment (i.e. 2.5s with the fix beats
3.5-4.2s without it, on the same degraded connection). Production (Render,
a real datacenter) very likely has a more stable path to OpenAI than a
local/home connection — the ~2.5s local reading isn't necessarily what a
real caller on the deployed app would experience, but this hasn't been
confirmed against Render directly.

**A filler/comfort tone was also built and then removed** (per explicit
request) — `apps/api/channels/voice/filler_audio.py` (a hand-rolled G.711
mu-law tone generator, since `audioop` was removed in Python 3.13) plus a
`play_filler_audio` hook in `adapter.py`, played immediately after the
Plivo/Twilio handshake to mask the dead-air gap while OpenAI connects. Net
diff after removal is zero — mentioned here only so the idea and its
rationale aren't lost if it's worth revisiting later.

**200-400ms for this specific delay (call-start) is not achievable** — established
directly: OpenAI's own model needs real time just to process instructions
and start generating the greeting, independent of any network cost. The
only way to get materially closer would be a pool of pre-warmed,
already-connected OpenAI sessions ready to be claimed instantly — real
ongoing cost (Realtime sessions bill by connected time) and real complexity
(session lifecycle, per-org instruction pre-loading, replenishment) — not
attempted, flagged as a possible future follow-up only.

### 3b. Turn-taking delay (caller finishes speaking → AI starts responding)

A different, initially-conflated metric — this is mid-call responsiveness,
not the one-time call-start cost. Good news found while investigating: the
large org script is sent to OpenAI **once**, at session start
(`session.update`) — a persistent Realtime session, unlike the stateless
per-call chat-completions path, so it is **not** re-processed on every
turn. Mid-call turns were never paying that cost.

**The real, direct lever**: [`_session_update_event`](apps/api/channels/voice/realtime_bridge.py#L111)'s
voice-activity-detection config had `silence_duration_ms: 500` — the
caller must go quiet for a full 500ms before their turn is even considered
over and a response starts generating, before any model processing begins.
Unlike `threshold` (0.6) and `prefix_padding_ms` (300ms) in the same
config — both deliberately raised from OpenAI's defaults specifically to
stop phone-line background noise from being misread as speech — this value
was just sitting at OpenAI's own default; changing it doesn't touch that
noise-avoidance tuning.

**The fix**: lowered `silence_duration_ms` from 500 to **300**. Direct
200ms cut on every mid-call turn. **Tradeoff, not free**: too low risks the
AI cutting in during a caller's natural mid-sentence pause (e.g. reading
out a phone number digit by digit) — 300 was picked as a middle ground, not
verified against a live call yet.

Beyond this config change, the remaining delay is OpenAI's own model
response/synthesis time — not something the app controls or can measure
without a live call.

### Status — not deployed

```
git status --short
 M apps/api/channels/voice/realtime_bridge.py
```

Uncommitted. All changes in this section live in that one file. All 275
backend tests pass locally (no dedicated new test for the voice-stream
changes — they need a live WebSocket to exercise directly; covered instead
by direct timing tests against the real OpenAI endpoint, described above,
which are not part of the repo).

---

## Net effect so far

- Real, confirmed, live: ~380-390ms/turn saved from the Redis fix (WhatsApp,
  part 1), plus a narrower win on WhatsApp turns where the model batches
  independent tool calls together (part 2).
- Written, tested, waiting on a go-ahead to ship: voice call-start delay
  reduced (amount varies with OpenAI's own connect-time variability — real
  but not to any specific target), and a direct 200ms cut on every mid-call
  voice turn (part 3).

## How to re-check latency

WhatsApp:

```
curl -H "X-Admin-Token: <ADMIN_TOKEN from .env>" https://veerox-o2en.onrender.com/diag/latency
```

`timings.redis_ping_ms` / `redis_set_ms` should read close to the current
~2ms; `recent_turns[].total_ms` reflects real WhatsApp traffic since the
last Redis rolling-list reset (resets whenever the underlying Redis database
changes, as it did during the cutover in part 1).
