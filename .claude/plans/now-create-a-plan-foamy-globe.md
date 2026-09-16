# License-Based Organization Access Control (replaces Razorpay billing)

> Once this plan is approved, the "What Gets Removed" checklist below will also be written to `docs/razorpay-billing-removal.md` in the repo as committed reference documentation, before implementation begins — so the full list of deleted Razorpay/billing code is recorded independently of the plan file.

## What Gets Removed (full checklist)

**Backend**
- `apps/api/db/models/plan.py` (entire file — `Plan` model, `PLAN_CODES`, `PLAN_RESOURCE_TYPES`, `RESOURCE_TYPE_LIMIT_KEY`)
- `apps/api/db/models/billing_payment.py` (entire file — `BillingPayment` model)
- `Org.plan_id`, `Org.billing_status`, `Org.plan_started_at`, `Org.free_plan_claimed_at`, `Org.resource_limits` columns (org.py)
- `apps/api/deps.py`: `_BLOCKED_BILLING_STATUSES`, `is_over_plan_limit`, `enforce_plan_limit`, `is_plan_feature_enabled`, `enforce_plan_feature`
- `apps/api/routers/billing.py` routes: `/usage`, `/orgs/{org_id}/payments`, `/plans` (GET/POST/PATCH/DELETE), `/available-plans`, `/status`, `/payments`, `/checkout-session`, `/verify-payment`, `/webhook`
- `apps/api/routers/billing.py` helpers: `_razorpay_client`, `_activate_paid_payment`, `_apply_resource_recharge`, `_process_event`
- `enforce_plan_limit`/`enforce_plan_feature` call sites: `apps/api/routers/admin.py:1897,3260,3454`; `apps/api/routers/team.py:158`; `apps/api/routers/follow_ups.py:30,44,67,102,134,153`
- `apps/api/config.py`: `razorpay_key_id`, `razorpay_key_secret`, `razorpay_webhook_secret`
- Tables `plans`, `billing_payments` (dropped via migration)
- Tests: `test_billing_webhook.py`, `test_checkout_and_verify_payment.py`, `test_plan_admin_endpoints.py`, Razorpay-specific parts of `test_platform_admin.py`

**Frontend**
- `apps/web/src/app/(dashboard)/billing/` and `.../billing/upgrade/`
- `apps/web/src/app/(auth)/choose-plan/`
- `apps/web/src/components/billing/*`: `billing-history-card.tsx`, `choose-plan-cards.tsx`, `credit-expired-modal.tsx`, `new-plan-dialog.tsx`, `plan-admin-table.tsx`, `platform-settings-panel.tsx` (verify not reused for non-billing settings first), `usage-warning-banner.tsx`
- `apps/web/src/lib/hooks/useBilling.ts`, `useAdminPlans.ts`
- `apps/web/src/components/nav.tsx`: `useBillingStatus` import, `/billing` nav item, `/billing` entry in `MEMBER_RESTRICTED_HREFS`, plan-limit display logic
- Plan/billing-status fields in `apps/web/src/lib/types.ts`

## Context

Veerox AI currently gates org access/usage through a Razorpay-integrated plan system (`Org.billing_status`, `Plan`, `BillingPayment`, checkout/webhook endpoints, per-metric `enforce_plan_limit`/`enforce_plan_feature` calls). Payment will now happen entirely outside the platform, negotiated manually with clients. The platform admin needs to manually issue, renew, extend, suspend, and reactivate a per-org license with an expiry date, from the Admin Panel — and once a license is invalid, the *entire* org (all its users) should lose access until the admin acts.

This is a full replacement, not an addition: the Razorpay payment integration, `Plan`-based usage/feature gating, and their UI are being removed, and org access is being re-architected around license status instead of billing status.

## Backend

### 1. Data model — `apps/api/db/models/org.py`
Add to `Org`:
- `license_status: str` — `"active" | "suspended" | "expired"`, default `"active"`.
- `license_expires_at: datetime | None`
- `license_issued_at: datetime | None`
- `license_notes: str | None` (free text for admin, e.g. "renewed via bank transfer")

Remove: `plan_id`, `billing_status`, `plan_started_at`, `free_plan_claimed_at`, `resource_limits` (org.py:20-49 per exploration).

Delete `apps/api/db/models/plan.py` and `apps/api/db/models/billing_payment.py` entirely, and their relationships/imports elsewhere.

### 2. Migration — `migrations/versions/<hash>_add_org_license_and_drop_billing.py`
Follows existing naming pattern (`<hash>_add_org_<field>.py` family). One migration that:
- Adds the four license columns to `orgs`.
- Drops `plans` and `billing_payments` tables.
- Drops `orgs.plan_id`, `orgs.billing_status`, `orgs.plan_started_at`, `orgs.free_plan_claimed_at`, `orgs.resource_limits`.
- Backfills existing orgs: `license_status='active'`, `license_issued_at=now()`, leave `license_expires_at` null (admin sets it explicitly) — or optionally seed from today + 30 days; confirm with user data expectations at implementation time if ambiguous.

### 3. Central enforcement — `apps/api/deps.py`
Remove `is_over_plan_limit`, `enforce_plan_limit`, `is_plan_feature_enabled`, `enforce_plan_feature` (deps.py:165-266) and `_BLOCKED_BILLING_STATUSES` (deps.py:150).

Add a new dependency, e.g. `enforce_org_license(org: Org = Depends(...))`, wired into the shared auth path where org is resolved (`resolve_request_org_id` / `verify_admin_or_session`, deps.py:299-369) so it runs on every org-scoped request without per-route changes. Logic:
- Skip check if `org.id` is the platform's own org (however the platform org is currently identified — reuse existing convention from `list_orgs`'s "excludes the platform's own org" logic in billing.py:176-244).
- If `license_status == "suspended"` or `license_status == "expired"` → raise `HTTPException(403, detail={"error": "license_inactive", "status": org.license_status, "expires_at": ...})`.
- Do NOT recompute expiry here (that's the background worker's job) — this stays a fast read-only check.

Remove all call sites of the deleted enforce functions:
- `apps/api/routers/admin.py:1897, 3260, 3454`
- `apps/api/routers/team.py:158`
- `apps/api/routers/follow_ups.py:30,44,67,102,134,153`

### 4. Background expiry worker
Add `apps/api/workers/license_expiry_worker.py`, following the existing scheduling pattern used by `apps/api/workers/campaign_dialer.py` / `follow_up_dispatcher.py` (check `main.py` for how those are started as periodic background tasks and mirror it). Runs on an interval (e.g. every 5–10 min): finds orgs where `license_status = 'active' AND license_expires_at < now()`, sets `license_status = 'expired'`. Manual `suspend` bypasses this — it's set directly by the admin action, not the worker.

### 5. Admin license actions — `apps/api/routers/billing.py`
Keep the general org CRUD already here (`list_orgs`, `update_org`, `delete_org`, `regenerate_admin_token` — billing.py:176-590) since it's not Razorpay-specific.

Remove Razorpay-specific pieces: `/orgs/{org_id}/payments` (545), `/plans` CRUD (599-660), `/available-plans` (713), `/status` (737), `/payments` (800), `/checkout-session` (823), `/verify-payment` (998), `/webhook` (1035), and helpers `_razorpay_client` (120), `_activate_paid_payment` (943), `_apply_resource_recharge` (900), `_process_event` (1083). Also remove `/usage` (126) since it's plan-limit specific.

Add new endpoints (admin-only, same auth guard as `list_orgs`):
- `POST /billing/orgs/{org_id}/license/issue` — body: `expires_at`, optional `notes`. Sets status active, issued_at=now.
- `POST /billing/orgs/{org_id}/license/renew` — body: `expires_at`. Sets new expiry, status→active.
- `POST /billing/orgs/{org_id}/license/extend` — body: `days` (or `expires_at`). Extends current expiry.
- `POST /billing/orgs/{org_id}/license/suspend` — body: optional `notes`. Sets status suspended immediately.
- `POST /billing/orgs/{org_id}/license/reactivate` — body: optional new `expires_at`. Sets status active (validates expiry isn't already in the past, otherwise require a new date).

Add corresponding request/response schemas in `apps/api/schemas/admin.py` (or a new `schemas/license.py`), and include `license_status`/`license_expires_at`/`license_issued_at`/`license_notes` in `OrgAdminOut`.

### 6. Config cleanup — `apps/api/config.py`
Remove `razorpay_key_id`, `razorpay_key_secret`, `razorpay_webhook_secret` (config.py:33-36).

### 7. Tests
Remove: `test_billing_webhook.py`, `test_checkout_and_verify_payment.py`, `test_plan_admin_endpoints.py`, and Razorpay-specific parts of `test_platform_admin.py`. Add `test_org_license.py` covering: issue/renew/extend/suspend/reactivate endpoints, the `enforce_org_license` dependency blocking a suspended/expired org's requests, and the expiry worker flipping status.

## Frontend

### 1. Remove billing/plan UI
- Pages: `apps/web/src/app/(dashboard)/billing/`, `apps/web/src/app/(dashboard)/billing/upgrade/`, `apps/web/src/app/(auth)/choose-plan/`.
- Components: `apps/web/src/components/billing/*` (`billing-history-card.tsx`, `choose-plan-cards.tsx`, `credit-expired-modal.tsx`, `new-plan-dialog.tsx`, `plan-admin-table.tsx`, `platform-settings-panel.tsx`, `usage-warning-banner.tsx`) — check `platform-settings-panel.tsx` isn't used for non-billing settings before deleting wholesale.
- Hooks: `apps/web/src/lib/hooks/useBilling.ts`, `useAdminPlans.ts`.
- Nav: `apps/web/src/components/nav.tsx` — remove `useBillingStatus` import (32), `/billing` nav item (102), `/billing` from `MEMBER_RESTRICTED_HREFS` (142), plan-limit display logic (155/160).
- Types: remove plan/billing-status fields from `apps/web/src/lib/types.ts`.

### 2. License lockout UX
Inspect `apps/web/src/lib/query.ts`'s existing global error interception (currently likely triggers `credit-expired-modal` on 402/billing errors) and repurpose that same mechanism: on a `403` with `error: "license_inactive"`, show a full-screen blocking view (new component, e.g. `components/organizations/license-locked-screen.tsx`) telling the user their org's license has expired/been suspended and to contact the admin — instead of normal app content.

### 3. Admin license management UI
Extend the existing admin orgs screen (`apps/web/src/app/.../organizations/page.tsx` + `apps/web/src/lib/hooks/useAdminOrgs.ts`):
- Add `license_status` / `license_expires_at` columns to the org list.
- Add a "Manage License" dialog (new component, modeled after `new-org-dialog.tsx`'s form patterns) with actions for issue/renew/extend/suspend/reactivate, calling new hooks in `useAdminOrgs.ts` (`useIssueLicense`, `useRenewLicense`, `useExtendLicense`, `useSuspendLicense`, `useReactivateLicense`) that hit the new `/billing/orgs/{org_id}/license/*` endpoints.

## Verification
- Backend: run `apps/api/tests/test_org_license.py` plus the existing admin/org test suites (`test_admin_endpoints.py`, `test_auth_endpoints.py`) to confirm no regressions from removed billing dependencies.
- Manually issue a license with a past `expires_at` (or run the worker function directly), confirm a member of that org gets a 403 on any org-scoped API call, and confirm the platform org is unaffected.
- Frontend: run the app, confirm the Billing nav item is gone, confirm the admin org table shows license status/expiry and the manage-license dialog's actions round-trip correctly, and confirm a locked-out org shows the new blocking screen instead of the dashboard.
