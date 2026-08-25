# Plan Model

How Veerox's subscription plans, recharges, and usage limits work — the
`Plan`/`Org` data model, the billing lifecycle that touches it, and how
limits get enforced.

## Core idea: recharge-based, not calendar-based

There is no monthly billing cycle. Buying a plan is a **one-time payment**
that grants a bucket of credits (call minutes, WhatsApp messages, team
seats, campaigns). Those credits are consumed until they run out; nothing
resets on a timer, and there's no provider-side auto-renew. The frontend
prompts an org to check out again once it's out of credits.

This is a deliberate simplification: Razorpay Subscriptions require a
dashboard-side product activation that blocked local/test-mode setup, so
billing uses plain one-time Razorpay **Orders** instead
(`apps/api/routers/billing.py`).

## Data model

### `Plan` — [apps/api/db/models/plan.py](apps/api/db/models/plan.py)

The platform-wide catalog, shared by every org (not org-scoped). Managed by
platform admins only (`verify_platform_admin`), exposed at `/billing/plans`.

| Column | Meaning |
|---|---|
| `code` | Unique slug, e.g. `basic` / `pro` / `premium` (see `PLAN_CODES`) |
| `name` | Display name |
| `price_cents` | One-time price, not a recurring fee |
| `limits` | JSON dict, e.g. `{"max_seats": 5, "max_campaigns": 20, "automated_followups": true}` |
| `is_active` | Inactive plans are hidden from `/billing/available-plans` but not deleted |
| `resource_type` | `None` for a full plan, or one of `PLAN_RESOURCE_TYPES` for a single-resource recharge SKU |

A `Plan` is either:
- **A full subscription bundle** (`resource_type is None`) — covers all four
  resources at once. Buying it replaces the org's `plan_id` and resets every
  resource's usage window.
- **A single-resource recharge/top-up SKU** — its `limits` JSON carries
  exactly one of `PLAN_RESOURCE_TYPES` with the amount to add. Buying it
  tops up only that one resource, leaving the other three untouched.

`PLAN_RESOURCE_TYPES`: `max_call_minutes`, `max_whatsapp_messages`,
`max_team_members`, `max_campaigns`.

`RESOURCE_TYPE_LIMIT_KEY` maps a recharge SKU's `resource_type` to the real
limit key it increments. Only `max_team_members` differs from its own name
— it maps to `max_seats`, the pre-existing limit key used everywhere else
(routers/team.py, deps.py, full-plan `limits` JSON).

### `Org` fields — [apps/api/db/models/org.py](apps/api/db/models/org.py)

| Column | Meaning |
|---|---|
| `plan_id` | FK to the org's current full plan (nullable) |
| `billing_status` | `trialing` → `active` → `past_due` \| `canceled` \| `incomplete` |
| `plan_started_at` | When the current plan took effect (last full recharge). Usage is counted from here — moving it forward is what restores credits |
| `resource_limits` | Org-specific effective limits, overriding `Plan.limits` once populated by any recharge. `NULL` = never touched by the recharge scheme yet |

## Effective limits: `Org.resource_limits` vs. `Plan.limits`

`deps.py::effective_limits(org, plan)`:
- If `org.resource_limits` is set, use it wholesale (it's a complete
  snapshot, key by key).
- Otherwise fall back to `plan.limits` wholesale.

`resource_limits` is `NULL` until the *first* recharge under this scheme,
so pre-existing orgs are unaffected until they next buy something. It's
seeded in two ways:
- **Full plan purchase**: seeded as a full copy of `purchased_plan.limits`.
- **First single-resource recharge on an org with no `resource_limits`
  yet**: seeded from the org's *current* plan (so untouched resources keep
  their base-plan limits), or all-zero if the org has no plan at all (so a
  first purchase of just call minutes doesn't imply free-unlimited
  everything else — a missing key reads as "unlimited").

## Usage aggregation — [apps/api/core/usage.py](apps/api/core/usage.py)

`get_credit_usage(db, org_id)` computes usage **live** from
`messages`/`conversations`/`call_campaigns` rather than a maintained
counter, filtered to `>= Org.plan_started_at` (or unfiltered if
`plan_started_at` is `NULL` — conservative: no free credits window).

- **Call minutes**: prefers `Conversation.ended_at - started_at` (set
  in-process when the realtime voice bridge disconnects); falls back to
  Plivo's `recording_duration_secs` webhook only if the bridge crashed
  before closing the conversation.
- **WhatsApp messages**: count of `Message` rows with `channel == "whatsapp"`.
- **Campaigns**: count of `CallCampaign` rows.

Note: a single-resource recharge does **not** move `plan_started_at`, so
usage on the other three resources keeps accumulating from the last full
reset — only the recharged resource's *limit* goes up.

## Enforcement — [apps/api/deps.py](apps/api/deps.py)

- `is_over_plan_limit(db, org_id, metric, current_count)` — non-raising
  check used by background workers (campaign dialer, WhatsApp dispatcher).
  Blocks *every* metric if `billing_status` is `past_due`/`canceled`/
  `incomplete`; otherwise blocks just the one metric once
  `current_count >= effective_limits[metric]`. The platform admin's own org
  is always exempt. An org with no plan and no `resource_limits` yet is
  treated as unlimited (defensive backstop — the real gate is the
  frontend's `/choose-plan` redirect).
- `enforce_plan_limit(...)` — same check, raises HTTP 402.
- `is_plan_feature_enabled` / `enforce_plan_feature` — boolean feature
  flags living as keys in `Plan.limits` (e.g. `"automated_followups": true`),
  gated the same defensive-backstop way. Raises HTTP 403.

## Billing lifecycle — [apps/api/routers/billing.py](apps/api/routers/billing.py)

1. `POST /billing/checkout-session` — looks up the `Plan` by code.
   - `price_cents == 0`: no payment needed, plan/recharge applied
     immediately.
   - Otherwise creates a Razorpay Order, records a `BillingPayment` row
     with `status="created"`.
2. Payment confirmation, either path lands on `_activate_paid_payment`
   (idempotent on `payment.status == "paid"`, so both paths can fire safely):
   - `POST /billing/verify-payment` — client-side confirmation via Razorpay
     Checkout, signature-verified. Works without a publicly reachable
     webhook (useful for local/ngrok dev).
   - `POST /billing/webhook` — Razorpay server webhook
     (`payment.captured` / `payment.failed`), signature-verified, events
     logged to `billing_events` keyed by `X-Razorpay-Event-Id` for replay
     safety.
3. `_activate_paid_payment`:
   - Full plan → `org.plan_id`, `plan_started_at = now`, `resource_limits`
     reset to a full copy of the plan's limits, `billing_status = "active"`.
   - Recharge SKU → `_apply_resource_recharge` adds the purchased amount to
     just that one key in `resource_limits`; `plan_id`/`plan_started_at`
     untouched.
4. `payment.failed` only marks that one `BillingPayment` row `failed` — it
   never downgrades the org, since any previously-paid recharge stands on
   its own.

## Admin endpoints

- `GET/POST /billing/plans`, `PATCH/DELETE /billing/plans/{code}` — catalog
  CRUD, `verify_platform_admin` only (`X-Admin-Token` or
  `AccountUser.is_superuser`). Deleting a plan in use by any org is blocked.
- `GET /billing/available-plans` — active plans only, any authenticated org
  member (backs the "Choose plan" cards).
- `GET /billing/status`, `GET /billing/usage`, `GET /billing/payments` —
  self-service, any org member (read-only).
- `GET /billing/orgs`, `GET /billing/orgs/{id}/payments` — platform-wide org
  directory, admin only.

## Frontend

- [apps/web/src/components/billing/choose-plan-cards.tsx](apps/web/src/components/billing/choose-plan-cards.tsx) — plan picker (`/billing/available-plans`)
- [apps/web/src/components/billing/new-plan-dialog.tsx](apps/web/src/components/billing/new-plan-dialog.tsx) — admin create/edit plan form
- [apps/web/src/components/billing/plan-admin-table.tsx](apps/web/src/components/billing/plan-admin-table.tsx) — admin catalog table
- [apps/web/src/lib/hooks/useAdminPlans.ts](apps/web/src/lib/hooks/useAdminPlans.ts) — admin plan CRUD hook

## Key edge cases to remember

- A plan-less org (never onboarded, or pre-backfill) reads as **unlimited**
  everywhere in `deps.py`, not blocked — enforcement here is a backstop,
  not the primary gate.
- `resource_limits == NULL` is meaningfully different from
  `resource_limits == {}` — `NULL` means "fall back to `Plan.limits`
  wholesale"; once it's a dict, it fully overrides the plan, key by key.
- A missing key in the effective limits dict means **unlimited** for that
  metric, not zero.
- Buying a full plan always resets `plan_started_at` (and therefore the
  usage window) for *all* resources; buying a single-resource recharge
  never does, for any resource.
