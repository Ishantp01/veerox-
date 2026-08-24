# Resource-specific recharge (Call Minutes / WhatsApp / Team Members / Campaigns)

## Context

Today every successful Razorpay payment goes through `_activate_paid_payment()` in `apps/api/routers/billing.py`, which unconditionally reassigns the org's `plan_id` and resets `plan_started_at`. Since credits aren't stored balances — `apps/api/core/usage.py::get_credit_usage()` computes "used" live by counting `Conversation`/`Message`/`CallCampaign` rows since `Org.plan_started_at`, compared against `Plan.limits[metric]` — resetting that one timestamp simultaneously refills **all four** resources (call minutes, WhatsApp messages, team seats, campaigns) at once, because there is currently only one purchasable thing in the system: a `Plan` row, which is always a full 4-resource bundle. There is no field anywhere distinguishing "buy the whole plan" from "top up just this one resource."

The fix: introduce a lightweight "this Plan row is a single-resource top-up, not a full bundle" flag, and an org-level override that recharges can bump additively without disturbing the other three resources or the full-reset timestamp.

## Design

**`apps/api/db/models/plan.py`**
- Add `PLAN_RESOURCE_TYPES = ("max_call_minutes", "max_whatsapp_messages", "max_team_members", "max_campaigns")`, mirroring the existing unenforced `PLAN_CODES` constant. `"max_team_members"` is used here instead of the existing `"max_seats"` limits key purely because it reads more clearly as a purchase-type label — see the translation map below for why this does **not** touch the real `max_seats` enforcement key used everywhere else in the app.
- Add `RESOURCE_TYPE_LIMIT_KEY = {"max_call_minutes": "max_call_minutes", "max_whatsapp_messages": "max_whatsapp_messages", "max_team_members": "max_seats", "max_campaigns": "max_campaigns"}` — maps a recharge SKU's `resource_type` label to the actual key in `Plan.limits`/`Org.resource_limits` it increments. Every existing call site (`apps/api/routers/team.py::invite_member`, `apps/api/deps.py`, `get_billing_usage`'s `max_seats` field, the admin plan-limits JSON convention for *full* plans, etc.) already reads/writes `"max_seats"` and is untouched by this change — only the new recharge-SKU `resource_type` value and that one SKU's own `limits` JSON use the friendlier `"max_team_members"` name; it's translated back to `"max_seats"` the moment it's merged into `Org.resource_limits`. This is the only reason the translation map exists: to add a clearer label with zero risk to existing `max_seats`-keyed code.
- Add `resource_type: Mapped[str | None] = mapped_column(String(30), nullable=True)`. `None` = full subscription plan (unchanged behavior). One of the four `PLAN_RESOURCE_TYPES` values = a recharge SKU; its `limits` JSON carries just that one key (named after `resource_type` itself, e.g. `{"max_team_members": 5}` for a team-member recharge) with the top-up quantity.

**`apps/api/db/models/org.py`**
- Add `resource_limits: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)` (needs `from typing import Any` added to imports). This is the org's *effective* per-resource limits once populated, overriding the catalog `Plan.limits`. `None` = never touched by the new logic yet → fall back to `plan.limits` wholesale, so every existing org is unaffected until its first recharge under the new code.

**New Alembic migration** (`down_revision` = current head `a2b3c4d5e6f7_add_channel_to_campaign_targets`, pattern to follow: `c4d5e6f7a8b9_add_org_plan_started_at.py`)
- `op.add_column('plans', sa.Column('resource_type', sa.String(length=30), nullable=True))`
- `op.add_column('orgs', sa.Column('resource_limits', sa.JSON(), nullable=True))`
- No backfill needed — both null reproduce today's behavior exactly.

**`apps/api/deps.py`**
- Add a public helper next to `is_over_plan_limit`:
  ```python
  def effective_limits(org: Org, plan: Plan | None) -> dict[str, Any]:
      if org.resource_limits is not None:
          return org.resource_limits
      return dict(plan.limits) if plan is not None else {}
  ```
  (use the same lazy in-function `Org`/`Plan` imports the file already uses to dodge circular imports)
- Rewrite `is_over_plan_limit`'s query to an outer join so it still resolves when `Org.plan_id` is `None` but `resource_limits` is already set, **while exactly preserving today's documented "no plan → unlimited, billing_status not even checked" backstop**:
  ```python
  result = await db.execute(
      select(Plan, Org).outerjoin(Plan, Org.plan_id == Plan.id).where(Org.id == org_id)
  )
  row = result.first()
  if row is None:
      return False
  plan, target_org = row
  if plan is None and target_org.resource_limits is None:
      return False
  if target_org.billing_status in _BLOCKED_BILLING_STATUSES:
      return True
  limit = effective_limits(target_org, plan).get(metric)
  return limit is not None and current_count >= limit
  ```
  (A naive plain outer-join without the `plan is None and resource_limits is None` short-circuit would start blocking `past_due` orgs that have never had a plan — a real regression on the existing backstop. Must keep that check.)
- `is_plan_feature_enabled` is untouched — boolean feature flags aren't part of the recharge flow.

**`apps/api/routers/billing.py`**
- Extract a small shared helper, e.g. `_apply_resource_recharge(target_org: Org, purchased_plan: Plan, current_plan: Plan | None) -> None`, that seeds `target_org.resource_limits` from `current_plan.limits` if still `None`, then does the additive merge — **always reassigning a new dict**:
  ```python
  key = RESOURCE_TYPE_LIMIT_KEY[purchased_plan.resource_type]      # e.g. "max_team_members" -> "max_seats"
  amount = purchased_plan.limits.get(purchased_plan.resource_type, 0)  # read using the SKU's own label
  new_limits = dict(target_org.resource_limits or {})
  new_limits[key] = new_limits.get(key, 0) + amount                # write using the real enforcement key
  target_org.resource_limits = new_limits
  ```
  since plain JSON columns don't track in-place dict mutation. The read key (`resource_type`, e.g. `"max_team_members"`) and the write key (`RESOURCE_TYPE_LIMIT_KEY[...]`, e.g. `"max_seats"`) are deliberately different only for the team-members case — every other resource maps to itself.
- `_activate_paid_payment()`: fetch the purchased `Plan` (`payment.plan_id`). If `plan.resource_type is None`: today's behavior (`plan_id`, `billing_status="active"`, `plan_started_at=now`) plus `target_org.resource_limits = dict(plan.limits)` (fresh full snapshot, becomes the new baseline). Else: fetch the org's *current* plan (`target_org.plan_id`, a different row from the purchased one), call `_apply_resource_recharge(target_org, plan, current_plan)`, set `billing_status = "active"`, and **do not touch `plan_id` or `plan_started_at`** — leaving `plan_started_at` unmoved is what makes the recharge additive (already-counted usage for the other resources keeps counting against their unchanged limits; the recharged resource's limit goes up by exactly the purchased amount). The existing `if payment.status == "paid": return` idempotency guard stays untouched, ahead of all of this.
- `create_checkout_session()`'s free-plan (`price_cents == 0`) branch: apply the same `resource_type is None` branch/else split for consistency (free recharge SKUs likely don't exist in practice, but guard it anyway).
- `get_billing_usage()`: replace `limits = plan.limits` with `limits = effective_limits(target_org, plan)`.
- `get_billing_status()`: replace `limits=plan.limits` in the `PlanOut(...)` construction with `limits=effective_limits(target_org, plan)` — otherwise the dashboard shows stale catalog numbers after a partial recharge even though enforcement is correctly using the bumped limit.
- `_plan_admin_out()` and `list_available_plans()`: include `resource_type=plan.resource_type` in the returned `PlanAdminOut`/`PlanOut`.
- `create_plan()`: pass `resource_type=payload.resource_type` into the `Plan(...)` constructor.

**`apps/api/schemas/billing.py`**
- Add `resource_type: str | None = None` to `PlanOut`, `PlanAdminOut`, `PlanCreateIn`, `PlanUpdateIn` (the latter already has PATCH `exclude_unset=True` semantics, no extra handling needed).

## Why this covers all 4 resources uniformly

Team Members (enforced as `max_seats` in `apps/api/routers/team.py::invite_member` via `enforce_plan_limit` — that key name is unchanged and untouched by this plan) and Campaigns (`max_campaigns`, enforced in `apps/api/routers/admin.py`'s campaign-create route) both already funnel through the same `is_over_plan_limit`/`enforce_plan_limit` choke point as call minutes and WhatsApp messages — there's no separate mechanism to special-case. So a "Team Members recharge" is just a `Plan` row with `resource_type="max_team_members"` (translated to the real `"max_seats"` key on merge, per `RESOURCE_TYPE_LIMIT_KEY` above) and a "Campaigns recharge" is `resource_type="max_campaigns"` — no additional code path needed beyond what's described above, and no existing `max_seats` call site changes.

## Out of scope (flagged, not blocking)

Admin frontend to create/edit `resource_type` on a Plan (`apps/web/src/components/billing/new-plan-dialog.tsx`, `plan-admin-table.tsx`) and any customer-facing "buy a top-up" UI (`choose-plan-cards.tsx`) — the request is scoped to backend/Python logic, and `PATCH /billing/plans/{code}` + `POST /billing/plans` already let `resource_type` be set once it's added to the schemas above, so the backend is fully usable/testable via API before frontend catches up.

## Tests to add

- **`apps/api/tests/test_plan_admin_endpoints.py`**: create-plan round-trips `resource_type`.
- **`apps/api/tests/test_checkout_and_verify_payment.py`**:
  - Resource-only recharge bumps exactly one key in `org.resource_limits`, leaves the other three (seeded from a prior full-plan snapshot) unchanged.
  - Recharge does **not** move `org.plan_started_at` (contrast with the existing full-plan-activation test, which does).
  - Full-plan purchase still resets everything, and now also asserts `org.resource_limits == plan.limits`.
  - Recharge for a metric absent from the org's current plan lands at exactly the recharged amount (0 + recharge).
- **`apps/api/tests/test_billing_webhook.py`**: duplicate webhook delivery (same `X-Razorpay-Event-Id`) for a recharge applies the top-up exactly once, not twice — extend the existing dup-event pattern to assert on `org.resource_limits`, not just `BillingEvent` row count.
- **`apps/api/tests/test_usage_limits.py`**:
  - A `resource_type="max_team_members"` recharge raises the effective `max_seats` cap (via `RESOURCE_TYPE_LIMIT_KEY` translation), verified end-to-end through `team.py`'s invite flow.
  - Regression test locking in the `is_over_plan_limit` backstop: an org with `plan_id=None`, `resource_limits=None`, `billing_status="past_due"` is still treated as unlimited (guards against the outer-join query change silently starting to block it).

## Verification

- Run the extended/new tests: `apps/api/tests/test_plan_admin_endpoints.py`, `test_checkout_and_verify_payment.py`, `test_billing_webhook.py`, `test_usage_limits.py`.
- Manually walk Test Cases 1–5 from the request via the API (seed a full plan, verify-payment a call-minutes-only recharge, assert only `max_call_minutes` changed in `GET /billing/usage`; repeat for WhatsApp; repeat full-plan purchase and confirm all four reset).
