# Latency reduction — what changed

Date: 2026-09-02

Two separate pieces of work, done in this order. Part 1 is live in production.
Part 2 is written and tested but **not deployed yet** — it needs an explicit
commit + push before it has any real effect (see its own status note below).

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

## 2. Parallel tool-call dispatch (written + tested, NOT deployed)

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

### Status — not deployed

```
git status --short
 M apps/api/core/agent.py
 M apps/api/tests/test_agent_core.py
```

Uncommitted. Has **zero effect on production latency** until committed,
pushed to `main`, and Render redeploys.

---

## Net effect so far

- Real, confirmed, live: ~380-390ms/turn saved from the Redis fix (part 1).
- Written and tested, waiting on a go-ahead to ship: a smaller, narrower win
  on the subset of turns where the model batches independent tool calls
  together (part 2) — most turns are single-tool or no-tool and are
  unaffected by it.

## How to re-check latency

```
curl -H "X-Admin-Token: <ADMIN_TOKEN from .env>" https://veerox-o2en.onrender.com/diag/latency
```

`timings.redis_ping_ms` / `redis_set_ms` should read close to the current
~2ms; `recent_turns[].total_ms` reflects real WhatsApp traffic since the
last Redis rolling-list reset (resets whenever the underlying Redis database
changes, as it did during the cutover in part 1).
