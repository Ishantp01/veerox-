# AWS Multi-Tenant Usage Metering & Chargeback System

## Context

Veerox already runs as a single shared FastAPI/Postgres/Redis deployment (`apps/api`) serving every client org — this is confirmed, not just a target: an earlier attempt today to give each client its own deployment (commit `e1a7a8d`, Caddyfile/Docker-compose templates + a provisioning script) was written and then reverted (`a5f84a3`) in this same session. The requested "one central shared deployment, isolate by organizationId" architecture is exactly the state already reverted back to — nothing to change there.

What already exists and will be **reused, not rebuilt**:
- **Org isolation**: `apps/api/db/models/org.py` (`Org`), `org_membership.py`, and `deps.py`'s `CurrentOrgDep` / `RequestOrgDep` / `AnalyticsScopeDep` / `MemberScopeDep` — every write/read already resolves `org_id` server-side from a session or `X-Admin-Token`, never trusting a frontend-supplied id.
- **Licensing**: `Org.license_status` (active/suspended/expired) + `deps.enforce_org_license` (blocks a whole org) + `Org.enabled_features` + `deps.require_feature` (per-module gating) + `workers/license_expiry_worker.py` (auto-expiry) + `_org_is_platform_admin_owned` (platform org is always exempt). This already satisfies requirement §11 — no new licensing concept needed (per your answer), only reuse.
- **Provider credential isolation**: per-org encrypted columns directly on `Org` (`plivo_auth_id`/`plivo_auth_token_encrypted`, `twilio_*`, `meta_*`, `openai_api_key_encrypted`) via `core/crypto.py` (Fernet) + `core/org_credentials.py` / `core/org_openai_key.py` resolvers. **I will not introduce a separate generic `provider_credentials` table** — that would duplicate a mechanism that already works and risk breaking every call/WhatsApp send site that reads these columns today. New usage metering will read credentials the same way everything else does.
- **Webhook org resolution**: `channels/voice/webhook.py` (`_resolve_org_by_number` / `_resolve_org_by_last_contact`) and `channels/whatsapp/webhook.py` (`_resolve_org_by_phone_number_id`, HMAC verified per-org) already never trust a client-asserted org id. New metering hooks into these, doesn't replace them.
- **Background jobs**: no Celery/queue — plain `asyncio.create_task` poll loops started in `main.py`'s `lifespan`, one file per worker under `apps/api/workers/`, e.g. `license_expiry_worker.py` (300s interval, single UPDATE statement, try/except + `record_error()`). New aggregation/import/allocation/billing jobs follow this exact pattern — no new infra.
- **Migrations**: Alembic, one file per change under `migrations/versions/`, descriptive slug filenames (see `e3c4d5f6a7b8_add_billing_tables.py` for the existing billing-adjacent migration).
- **Tests**: pytest + pytest-asyncio, SQLite in-memory for the ORM, one file per concern under `apps/api/tests/`, `conftest.py`'s `_no_platform_channel_credentials_leak` fixture pattern for isolation tests.

What's genuinely missing and is the actual scope of this task: a normalized, immutable **usage event** ledger; daily/monthly **aggregation**; **AWS actual cost import** + **cost pools**; a documented **allocation engine** splitting shared AWS cost by org; **invoices/adjustments** with period locking; and the **org + owner dashboards** to show all of it. That's what this plan builds.

---

## 1. Database — new tables only (Alembic migration `add_usage_metering_and_billing`)

New models under `apps/api/db/models/` (added to `db/models/__init__.py` alongside the existing ones):

- **`UsageEvent`** (`usage_events`) — the immutable ledger row exactly per spec: `id` (uuid pk), `event_id` (str, **unique**, deterministic — see §2), `org_id` (FK `orgs.id`, indexed), `request_id` (nullable), `service`, `usage_type`, `quantity` (Numeric), `unit`, `provider` (nullable), `source`, `started_at`/`ended_at` (nullable), `metadata_json` (JSON, nullable — never raw secrets/message bodies, enforced by convention + a test), `created_at`. Indexes: `(org_id, created_at)`, `(org_id, service, usage_type, created_at)`, unique on `event_id`. No update/delete path is ever exposed — corrections happen via `UsageAdjustment`, never by mutating a row.
- **`UsageDaily`** (`usage_daily`) — `org_id`, `usage_date`, `service`, `usage_type`, `total_quantity`, `event_count`, `estimated_cost` (nullable Numeric), `created_at`/`updated_at`. Unique on `(org_id, usage_date, service, usage_type)` — the upsert key that makes the aggregator idempotent.
- **`UsageMonthly`** (`usage_monthly`) — `org_id`, `billing_period` (`YYYY-MM` str or first-of-month date), `service`, `usage_type`, `total_quantity`, `allocated_aws_cost`, `third_party_cost`, `platform_fee`, `total_estimated_charge`, `calculation_status` (`pending`/`calculated`/`locked`), `allocation_version`, `created_at`/`updated_at`. Unique on `(org_id, billing_period, service, usage_type)`.
- **`CostRate`** (`cost_rates`) — documented per-unit rates used to turn third-party usage into `estimated_cost` (e.g. `service="ai", usage_type="ai_input_tokens", provider="openai", unit_cost, currency, effective_from`). Admin-editable table, not hardcoded, so rate changes are auditable.
- **`AwsCostPool`** (`aws_cost_pools`) — `billing_period`, `aws_account_id`, `aws_service`, `pool_category` (compute/database/storage/network/serverless/queue/logging/misc), `total_cost`, `currency`, `source` (`cost_explorer`/`cur`/`manual`), `imported_at`.
- **`CostAllocation`** (`cost_allocations`) — `billing_period`, `org_id` (nullable — NULL row = the Shared/Unallocated bucket), `pool_category`, `allocated_cost`, `metric_used`, `metric_value`, `total_metric_value`, `is_estimated` (bool, always true except the unallocated row), `allocation_version`, `created_at`.
- **`Invoice`** (`invoices`) — `org_id`, `billing_period`, `usage_charge`, `platform_fee`, `total_amount`, `currency`, `status` (`draft`/`final`/`void`), `locked_at`, `generated_at`.
- **`UsageAdjustment`** (`usage_adjustments`) — `org_id`, `billing_period`, `reason` (`late_event`/`rounding`/`shared_overhead`/`manual`), `amount`, `related_event_id` (nullable), `created_by_account_user_id` (nullable), `created_at`. This is how a usage event that arrives after a period is locked gets reflected without ever rewriting the locked `UsageMonthly`/`Invoice` row.

All FKs to `orgs.id` with `ondelete="CASCADE"`, matching every existing org-owned table. No changes to `Org`, `OrgMembership`, or credential columns — those are correct as-is.

## 2. `recordUsage` — `apps/api/core/usage.py`

```python
async def record_usage(db, *, organization_id, event_id, service, usage_type, quantity, unit,
                        provider=None, source, request_id=None, started_at=None, ended_at=None,
                        metadata=None) -> UsageEvent | None
```
- Inserts a `UsageEvent`; on a unique-constraint violation on `event_id` (`IntegrityError`), catches it, logs `usage_event_duplicate_ignored`, and returns `None` — this is the idempotency guarantee, not an app-level "check then insert" (race-safe under concurrent workers).
- `event_id` is **caller-provided and must be deterministic** for retryable operations. Add a small helper `deterministic_event_id(*parts: str) -> str` (uuid5 over a stable namespace + joined parts, e.g. `deterministic_event_id("voice_call_ended", call_uuid)`, `deterministic_event_id("whatsapp_inbound", message_id)`, `deterministic_event_id("ai_completion", conversation_id, turn_index)`) so retries of the same call/webhook/job tick always produce the same id.
- `metadata` is validated against a small denylist of keys (`api_key`, `token`, `authorization`, `password`, `access_token`, `secret`) before being stored — raises in dev/tests, strips-and-logs in prod — as a defense-in-depth backstop for req §5's "never store secrets" rule, in addition to callers simply not passing them.
- Never raises for a well-formed duplicate; does raise for a genuinely malformed call (missing org_id, non-numeric quantity), since that's a programming error, not a retry.

## 3. Metering call sites (additive — no existing behavior changes)

Each site builds a deterministic `event_id` from data it already has:

- **Voice** — `channels/voice/webhook.py::answer` (call started, `service="voice"`, `usage_type="ai_request_count"` quantity 1, keyed on `call_uuid`); `channels/voice/realtime_bridge.py` (call connected + call ended + `voice_seconds` from the same duration math already used for logging/recording); `core/transcribe.py` (STT ops); `core/llm.py::chat_completion` (already returns `tokens_in`/`tokens_out` — add an optional `org_id` passthrough param so the agent layer can record `ai_input_tokens`/`ai_output_tokens`/`ai_request_count` right after each call without llm.py itself knowing about orgs).
- **WhatsApp** — `channels/whatsapp/webhook.py::receive_webhook` (inbound, keyed on Meta's message id) and `channels/whatsapp/client.py` (outbound send, keyed on the returned WAMID); `channels/whatsapp/adapter.py` (automation/AI processing duration).
- **Workers** — `campaign_dialer.py`, `whatsapp_dispatcher.py`, `follow_up_dispatcher.py` each record `worker_job_seconds` per tick (`service="compute"`), keyed on a tick timestamp + org, so shared-infra usage has a first-class signal independent of third-party call/message counts (per req §6's AWS-workload bullet).

All of these already have `org_id` in scope by the time they'd call `record_usage` (resolved the existing, trusted way) — this is purely an additive call, not a rework of any resolution logic.

## 4. Aggregation, import, allocation, billing — new workers under `apps/api/workers/`

Same asyncio-loop-in-lifespan pattern as `license_expiry_worker.py`, registered in `main.py`:

- **`usage_daily_aggregator.py`** — hourly tick; upserts `UsageDaily` for `usage_date = yesterday and today` (re-running today's partial day is safe — it's an upsert) from `UsageEvent`, applying `CostRate` to get `estimated_cost` where a rate exists.
- **`usage_monthly_aggregator.py`** — daily tick; upserts `UsageMonthly` from `UsageDaily` for the current open period only (never touches a `locked` row — a locked `UsageMonthly` is skipped and logged).
- **`aws_cost_importer.py`** — daily tick, **no-ops entirely unless `settings.aws_cost_import_enabled` is true and AWS creds are present** (per your answer — built now, inert until you add IAM creds later). Uses `boto3.client("ce").get_cost_and_usage(...)`, buckets results into `AwsCostPool` rows by service→category mapping, upsert on `(billing_period, aws_account_id, aws_service)`.
- **`cost_allocation_worker.py`** — after cost pools exist for a period, computes each org's share per category using the documented metric (compute→summed `worker_job_seconds`, database→`database_operations`, network→bandwidth bytes, storage→GB-month, serverless/queue→invocation/job counts, all pulled from that period's `UsageMonthly`), writes one `CostAllocation` row per org per category **plus one `org_id IS NULL` "Shared/Unallocated" row** for the remainder, all stamped with a bumped `allocation_version` — never overwrites a prior version's rows, so allocation rules are never silently changed retroactively (req §10).
- **`billing_worker.py`** / **`reconciliation_worker.py`** — implement the 8-step close sequence from req §16 as one admin-triggered flow (see §5's close-period endpoint) rather than a blind timer: import → aggregate → allocate → compare actual-vs-allocated → adjustments → lock (`UsageMonthly.calculation_status="locked"`, `Invoice` rows created) → done. A late `UsageEvent` after lock creates a `UsageAdjustment` instead of reopening the period.
- **`anomaly_detection_worker.py`** — daily tick, flags orgs whose day-over-day `UsageDaily.total_quantity` per service jumps past a simple threshold (e.g. 3x the trailing 7-day average) into a small `usage_anomalies` marker (reuse `UsageAdjustment`-style light table or just a structured log + admin-dashboard query over `UsageDaily` — decide at implementation time based on how much the owner dashboard needs to persist vs. compute on read).

## 5. API endpoints (all under existing router conventions — new `routers/usage.py` + additions to `routers/billing.py`)

- `GET /org/usage?period=current` and `GET /org/usage/breakdown?period=current` — `RequestOrgDep` (already license-enforced, already server-resolved org). Org self-service, no admin gate needed beyond that.
- `GET /org/billing/estimate?period=current` — reads `UsageMonthly` + `CostAllocation` for the caller's own org, labels the AWS component **"Estimated Infrastructure Usage"** in the response itself (not just the frontend) so no client of the API can mislabel it as an invoice.
- `GET /admin/organizations/:id/usage`, `GET /admin/aws-costs?period=current` — `verify_platform_admin` (existing dependency), platform-wide visibility per req §13.
- `POST /internal/usage-events` — direct `record_usage` passthrough for anything that can't call the Python function in-process (kept minimal since almost everything in this monolith *can* call it in-process); gated by the existing `X-Admin-Token` check only (no session path) — matches "must not be publicly accessible" without inventing a second secret.
- `POST /admin/billing/periods/:period/close` — `verify_platform_admin`, runs the reconciliation sequence from §4, refuses (409) if that period is already `locked`.
- **No new generic `POST /webhooks/:provider`** — the existing per-provider webhooks (`/webhook/whatsapp`, `/voice/answer` etc.) already do correct server-side org resolution; duplicating that behind a generic route would be a second, weaker implementation of the same thing. Metering is added *into* the existing handlers (§3) instead.

## 6. Dashboards (`apps/web`)

- **Org usage dashboard** — new `app/(dashboard)/usage/page.tsx` + `lib/hooks/useUsage.ts` (same thin-wrapper-over-`api.ts` pattern as every other hook in `lib/hooks/`), showing current period, trend, AWS allocation breakdown (labeled "Estimated Infrastructure Usage"), per-channel usage, platform fee, estimated total, billing history. Nav entry added the same way other dashboard pages register (check `components/layout/` shell).
- **Owner dashboard** — extend the existing `organizations/` platform-admin pages (already the right access-controlled area) with an "Organization Usage & AWS Reconciliation" view: actual AWS total, allocated total, shared/unallocated, top consumers, service-wise cost, anomalies, license status, margin. Reuses `useAdminOrgs`-style hooks.
- A client's usage payload is filtered by the same `RequestOrgDep`/`verify_platform_admin` split every other endpoint uses — no separate authorization mechanism to get wrong.

## 7. Tests (new files under `apps/api/tests/`, following existing per-concern-per-file convention)

- `test_usage_events.py` — `record_usage` insert + duplicate `event_id` is silently ignored (no double count) + secret-key metadata rejected.
- `test_usage_isolation.py` — org A cannot read org B's `/org/usage`; a frontend-supplied `org_id` on any usage/billing route is ignored in favor of the session's own (mirrors existing `test_org_features.py`/`test_router_auth_guard.py` patterns); anonymous call to `/internal/usage-events` is rejected.
- `test_usage_aggregation.py` — daily/monthly aggregator idempotency (running twice doesn't double totals), locked-period is skipped.
- `test_aws_cost_allocation.py` — allocation formula splits a pool proportionally, leaves a correct Shared/Unallocated remainder, stamps `allocation_version`.
- `test_billing_reconciliation.py` — period close sequence, late event after lock produces a `UsageAdjustment` and does not mutate the locked `UsageMonthly`/`Invoice`.
- `test_org_license.py` (existing file) gets a couple of added cases confirming usage/billing endpoints respect `enforce_org_license` and the platform-org exemption exactly like every other router — proving no new bypass was introduced.

## Verification

1. `uv run pytest apps/api/tests` — full existing suite must still pass unchanged (proves nothing broke), plus all new test files above.
2. `uv run mypy apps/api` (or whatever type-check command `pyproject.toml`/CI uses) — clean.
3. Manually hit `GET /org/usage?period=current` with two different orgs' session tokens and confirm disjoint data; hit `/admin/aws-costs` with a non-superuser session and confirm 403.
4. Run `usage_daily_aggregator`/`usage_monthly_aggregator` twice in a row against the same data and confirm `UsageDaily`/`UsageMonthly` totals don't double.
5. With `AWS_COST_IMPORT_ENABLED=false` (default), confirm the importer no-ops cleanly and nothing else in the app is affected.
