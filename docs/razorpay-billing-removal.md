# Razorpay / Billing Removal — License-Based Access Control

This document records everything removed as part of replacing the Razorpay-based
plan/billing system with manually admin-managed org licenses. See the implementation
plan for the full design (license fields, enforcement, worker, admin endpoints, UI).

## Backend

- `apps/api/db/models/plan.py` (entire file — `Plan` model, `PLAN_CODES`, `PLAN_RESOURCE_TYPES`, `RESOURCE_TYPE_LIMIT_KEY`)
- `apps/api/db/models/billing_payment.py` (entire file — `BillingPayment` model)
- `Org.plan_id`, `Org.billing_status`, `Org.plan_started_at`, `Org.free_plan_claimed_at`, `Org.resource_limits` columns (`apps/api/db/models/org.py`)
- `apps/api/deps.py`: `_BLOCKED_BILLING_STATUSES`, `is_over_plan_limit`, `enforce_plan_limit`, `is_plan_feature_enabled`, `enforce_plan_feature`
- `apps/api/routers/billing.py` routes: `/usage`, `/orgs/{org_id}/payments`, `/plans` (GET/POST/PATCH/DELETE), `/available-plans`, `/status`, `/payments`, `/checkout-session`, `/verify-payment`, `/webhook`
- `apps/api/routers/billing.py` helpers: `_razorpay_client`, `_activate_paid_payment`, `_apply_resource_recharge`, `_process_event`
- `enforce_plan_limit`/`enforce_plan_feature` call sites:
  - `apps/api/routers/admin.py:1897,3260,3454`
  - `apps/api/routers/team.py:158`
  - `apps/api/routers/follow_ups.py:30,44,67,102,134,153`
- `apps/api/config.py`: `razorpay_key_id`, `razorpay_key_secret`, `razorpay_webhook_secret`
- Database tables `plans`, `billing_payments` (dropped via migration)
- Tests: `test_billing_webhook.py`, `test_checkout_and_verify_payment.py`, `test_plan_admin_endpoints.py`, Razorpay-specific parts of `test_platform_admin.py`

## Frontend

- `apps/web/src/app/(dashboard)/billing/` and `.../billing/upgrade/`
- `apps/web/src/app/(auth)/choose-plan/`
- `apps/web/src/components/billing/*`:
  - `billing-history-card.tsx`
  - `choose-plan-cards.tsx`
  - `credit-expired-modal.tsx`
  - `new-plan-dialog.tsx`
  - `plan-admin-table.tsx`
  - `platform-settings-panel.tsx` (verified not reused for non-billing settings before deletion)
  - `usage-warning-banner.tsx`
- `apps/web/src/lib/hooks/useBilling.ts`, `useAdminPlans.ts`
- `apps/web/src/components/nav.tsx`: `useBillingStatus` import, `/billing` nav item, `/billing` entry in `MEMBER_RESTRICTED_HREFS`, plan-limit display logic
- Plan/billing-status fields in `apps/web/src/lib/types.ts`

## Replaced by

Org-level `license_status` / `license_expires_at` / `license_issued_at` / `license_notes`,
a central `enforce_org_license` dependency, a background expiry worker, and new
`/billing/orgs/{org_id}/license/*` admin endpoints (issue, renew, extend, suspend, reactivate).
