# Load & Concurrency — Where We Stand

Written after a capacity conversation about scaling to ~1000 orgs with up to 100 concurrent
calls. Nothing here has been load-tested — this is code analysis plus one config change, not a
measured result.

## The core fact: `MAX_CONCURRENT_CALLS` is a shared, platform-wide number

It is **not per-org**. All orgs share one Plivo account, one Twilio account, and one Meta WABA
(see `Org.plivo_phone_number` / `twilio_phone_number` / `whatsapp_phone_number_id` —
per-org fields hold which *number* is assigned, not a separate provider account). Plivo has no
concept of "your orgs"; it only sees one account placing calls, so its concurrent-call cap
applies across every org combined.

If true per-org guaranteed capacity is ever needed (so one org's campaign can't starve another's),
the real fix is **Plivo sub-accounts**, not an app-side per-org limit — an app-side number alone
doesn't create extra real capacity once more than one org is active at once.

## What actually gates concurrency today

`settings.max_concurrent_calls` (`MAX_CONCURRENT_CALLS` in `.env`, default 50) only gates the
**campaign dialer's** own outbound blast rate
(`workers/campaign_dialer.py::_count_calls_in_flight`, counts `CampaignTarget` rows with
`status="calling"` on voice campaigns). Two other call paths are **not** gated by it at all:

- Real inbound calls (someone dialing a Plivo/Twilio number) — no concurrency check in the app.
- Single admin-placed calls (`POST /admin/outbound/call`) — only checked against that org's
  `max_call_minutes` usage limit, not a concurrency counter.

So "50" is not a true system-wide ceiling today — actual simultaneous calls across all three
paths could exceed it.

## External provider ceilings (unverified, need checking directly with each provider)

1. **Plivo** — the `MAX_CONCURRENT_CALLS` comment in `config.py` says the account's real cap was
   "verified >= 6 by manual test on 2026-07-16." That's the only number we actually have
   evidence for. Confirm the real limit with Plivo support before assuming 50 (let alone 100)
   works.
2. **OpenAI Realtime API** — concurrent-session limits are tied to account usage tier. Not
   checked.
3. **ElevenLabs** (if voice cloning is in use — see `channels/voice/elevenlabs_client.py`) —
   concurrent-generation limits are plan-tied. We already hit a plan restriction once
   (`payment_required` on a library voice) on this account, so the current tier is known to be
   limited.

## App-side bottlenecks found

1. **DB connection pool was undersized.** `db/session.py` was `pool_size=10, max_overflow=20`
   (30 total). Each active call opens a short-lived `AsyncSessionLocal()` repeatedly over its
   lifetime — per tool call, per transcript persist, and a usage-limit check every 20s
   (`realtime_bridge.py::_watch_usage_limit`). At 100 concurrent calls that's a real recurring
   burst of checkouts. **Changed to `pool_size=30, max_overflow=50` (80 total)** on
   2026-08-19. Neon's pooled endpoint fronts this with PgBouncer in transaction-pooling mode, so
   this doesn't multiply real Postgres connections 1:1 — but Neon's own PgBouncer pool has its
   own ceiling too, not verified from here.
2. **Single process, no horizontal scaling.** `Dockerfile` runs one `uvicorn` process, no
   `--workers`, no multi-instance config in-repo. Render's actual instance count/size lives in
   their dashboard, invisible from the codebase. Async I/O is the right shape for many
   concurrent WebSocket bridges, but there's no redundancy — one crash/restart drops every live
   call at once — and real CPU/memory headroom is unverified.
3. **No fairness between orgs.** Nothing stops one org's campaign from claiming the entire
   shared `MAX_CONCURRENT_CALLS` budget while another org's queued calls wait.

## Action items (roughly in order)

1. Confirm Plivo's actual concurrent-call limit with their support — this is the real ceiling,
   no app-side change affects it.
2. Confirm OpenAI Realtime's concurrent-session limit for the account in use.
3. Confirm ElevenLabs' concurrent-stream limit for the account in use, if that path stays live.
4. Confirm Render instance size/plan (CPU, memory, instance count).
5. Run an actual load test simulating N concurrent calls against a non-prod environment — every
   number above this point is analysis, not a measurement.
6. If per-org guaranteed capacity matters: evaluate Plivo sub-accounts, or add explicit
   per-org reservation/fairness logic to `campaign_dialer.py`'s concurrency gate.
