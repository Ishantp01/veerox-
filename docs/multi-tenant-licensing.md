# Multi-tenant licensing, organization provisioning, and feature allocation

Status: application changes implemented and unit/integration-tested locally
against SQLite (this repo's existing test convention). **No AWS
infrastructure has been created, no production migration applied, and no
client has actually been deployed** — those steps require explicit
approval and real credentials.

This design was originally built around signed JWT licence assertions and
was subsequently simplified to **direct, authenticated server-to-server
HTTPS licence validation** (a client calls the owner API and trusts the
live response, over HTTPS + a bearer token — no locally-verified signature,
no signing keypair anywhere in the system). Everything below describes the
current, simplified design.

## 1. What existed before this change

Before this change, Veerox ran as a single shared-database, multi-tenant
application: one FastAPI deployment (`apps/api`), one Postgres database
holding every org's data (`Org`, `OrgMembership`, etc.), one EC2 host
(`.github/workflows/deploy-aws.yml` SSHes in and rebuilds one docker-compose
stack: `api` + `web` + `caddy`). Licensing already existed but was purely
local: `Org.license_status`/`license_expires_at`/`enabled_features`, enforced
by `apps/api/deps.py`'s `enforce_org_license`/`require_feature`, managed
through `apps/api/routers/billing.py`.

There was no concept of a *separate* client deployment, no deployment
credential mechanism, and no synchronization engine — because there was
nothing to synchronize to.

## 2. What this change adds

A deployment-mode switch (`DEPLOYMENT_MODE=owner|client`, `apps/api/config.py`)
lets the SAME codebase, with the SAME configuration schema, run as either:

- **Owner deployment** (`owner`, the default — matches the existing
  production deployment unchanged): runs the central licence API
  (`apps/api/routers/platform.py`) and the existing licence admin endpoints
  (`apps/api/routers/billing.py`, now also writing an audit trail and a
  synchronization record).
- **Client deployment** (`client`): a separate application + database for
  one client org. Never registers `billing.router`/`platform.router`/the
  cross-org ticket queue at all ("must not expose platform-owner
  administration endpoints" — enforced in `apps/api/main.py::create_app`,
  not just hidden in the frontend). Instead mounts
  `apps/api/routers/provisioning.py` and runs
  `apps/api/workers/central_sync_worker.py`, which periodically calls the
  owner's licence API directly over HTTPS and caches the result
  (`apps/api/core/license_cache.py`) — this cache is what `deps.py`'s
  licence/feature checks consult in this mode.

### Data model (owner database)

- `Org` (`apps/api/db/models/org.py`) gains `central_org_ref` (the stable
  cross-deployment identity), `deployment_status`
  (`pending_deployment`/`provisioned`), `config_version` (bumped on every
  change that must reach a client), and `client_deployment_id`.
  `ORG_LICENSE_STATUSES` gains `"revoked"`.
- `ClientDeployment` (new) — one row per registered client backend:
  `token_hash` (SHA-256 of its bearer token — the hash IS the lookup key,
  there is no separate public identifier), `status`
  (`pending`/`active`/`revoked`), `config_version`, `last_seen_at`,
  `last_sync_at`/`last_sync_status`/`last_sync_error`.
- `LicenseAuditEvent` (new) — append-only history behind every licence
  action (`issued`/`renewed`/`extended`/`suspended`/`revoked`/`reactivated`),
  since `Org`'s own columns only ever hold the current values.
- `SyncEvent` (new) — a durable record of a pending organization/feature
  change for a deployment (`org_provision`/`feature_sync`/`license_update`),
  with `config_version` and `status`. Purely bookkeeping/audit now — a
  client applies changes by pulling `GET /platform/sync/pending` itself,
  not by the owner pushing this row anywhere.

### Direct HTTPS licence validation

There is no signed token anywhere in this design. `GET /platform/license`
(owner side, `routers/platform.py`) returns plain structured JSON:

```json
{
  "organization": {"central_org_ref": "...", "name": "..."},
  "deployment_id": "...",
  "license": {"status": "active", "valid_from": "...", "expires_at": "..."},
  "enabled_features": ["crm", "leads"],
  "config_version": 3
}
```

A client deployment calls this directly over HTTPS, authenticated with its
own bearer token (`Authorization: Bearer <token>`,
`core/deployment_auth.py`), and trusts the response because the connection
was HTTPS + authenticated + the response's own `organization.central_org_ref`
matches what this deployment is configured for — not because of any
embedded cryptographic signature. `apps/api/core/license_cache.py` is the
client-side runtime layer that calls this, validates the response shape and
identity, and caches the result for a **fixed 5-minute constant**
(`CACHE_TTL_SECONDS` — a code constant, not an environment variable), capped
at the licence's own actual expiry if that's sooner. Refreshes are
coalesced via an `asyncio.Lock` (concurrent requests never trigger duplicate
outbound calls) and scheduled with jitter
(`apps/api/workers/central_sync_worker.py`).

`deps.py`'s `is_org_license_active`/`is_org_feature_enabled` read this cache
instead of the local `Org` row when `DEPLOYMENT_MODE=client` — a successful
refresh (favorable OR unfavorable) always overwrites the cache immediately,
so a confirmed suspension/revocation takes effect on the very next refresh.
A failed refresh (network error, timeout, bad response) never touches the
existing cache entry — it's not extended, not cleared, just left as-is;
once its own expiry passes with no successful refresh since, access blocks
outright. **There is no offline grace period.**

### One-click organization creation + automatic deployment registration

`POST /platform/organizations` does all of this in a single transaction —
there is no separate "register deployment" button or API call for the
normal path:

1. Creates the org + its allocated features + an optional first licence.
2. Creates its default `ClientDeployment` row.
3. Generates a bearer token with `secrets.token_urlsafe(32)` (256 bits of
   CSPRNG randomness) and stores server-side **only its SHA-256 hash**
   (`core/security.py::hash_token`, the same one-way convention already
   used for `AccountUser.token_hash`) — never recoverable, never logged.
4. Records the initial `org_provision` `SyncEvent`.

The response's `setup` object shows the raw token **exactly once** —
`{central_org_ref, license_api_url, deployment_token, env_snippet}`, with
`env_snippet` a copy-pasteable block using the exact shared `.env` variable
names (`DEPLOYMENT_MODE`, `DEFAULT_ORG_ID`, `LICENSE_API_URL`,
`LICENSE_API_TOKEN`, `DATABASE_URL=<client-database-connection>` — always a
placeholder, never the owner's own database credentials). The endpoint sets
`Cache-Control: no-store`; no other endpoint ever returns a token again.

**Duplicate-submission safety**: an optional `idempotency_key` on the
request — a resubmit (double-click, retried request after a dropped
response) with the same key returns the already-created organization
(`setup: null`, since the token was already shown once) instead of creating
a second org + deployment + token. Covers both the simple case (a SELECT
before INSERT finds the existing row) and the genuine race (two requests
past that SELECT before either commits — the loser's INSERT hits the
`orgs.setup_idempotency_key` unique constraint, caught and turned into
"return the winner's row").

**Existing organizations** (created before this feature, or any other way
that left them without a deployment) can have one prepared without
touching business data via `POST /platform/organizations/{id}/deployments`
— the same underlying registration logic, exposed as an explicit "prepare
setup" action for exactly this case. It's refused if a deployment already
exists (a security-relevant credential swap is never silent) — either revoke
first, or use the token-replacement action below.

**Token replacement**: `POST .../deployments/regenerate-token` — the
owner-only "generate replacement setup token" action, used ONLY when the
original token was lost before the client applied it (or needs invalidating
for security reasons). This is NOT part of ordinary edits, renewals,
synchronization, or restarts — none of those ever touch the token. The old
token stops working the instant this commits (only one hash is ever
stored); the client's `.env` must be updated and the app restarted.
**Revocation**: `POST .../deployments/revoke` — rejects the deployment's
token immediately regardless of whether it still hashes correctly.

The owner never looks a deployment up by anything the caller submits: `GET
/platform/license` etc. resolve the org **from the hash of the presented
token** (an indexed equality lookup — the hash is the only lookup key).

### Synchronization

Now entirely client-pull, never owner-push — removing this asymmetry (the
earlier design needed the owner to hold each deployment's secret
*recoverably* so it could authenticate an outbound push) is what let
deployment credentials become simple one-way-hashed bearer tokens.

- `routers/platform.py`'s `allocate_features`/`register_deployment` and
  `routers/billing.py`'s licence actions all, in the SAME transaction as
  the change itself, bump `Org.config_version` and insert a `SyncEvent` row
  (transactional local updates + durable events).
- `GET /platform/sync/pending` (bearer-token authenticated) returns the
  org's current configuration if the calling deployment's own recorded
  `config_version` is behind the org's, else `null`.
- `core/provisioning_apply.py::apply_provisioning_locally` is idempotent:
  it matches on `central_org_ref` (never a locally-generated primary key)
  and rejects (`applied=False`, not an error) any payload whose
  `config_version` isn't strictly greater than what's already applied —
  this is what stops a delayed retry of an old pull from clobbering a newer
  one, and what makes a duplicate application a no-op. Called in-process by
  `workers/central_sync_worker.py` after a successful authenticated pull —
  there is no longer an inbound HTTP write endpoint for this at all (the
  earlier design's `POST /provisioning/apply`, authenticated by a shared
  secret, is removed rather than reworked, since removing signing keys also
  removed the credential story that authenticated that direction).
- `POST /platform/sync/ack` records acknowledgement in the owner service,
  moving `Org.deployment_status` to `"provisioned"` and updating
  `ClientDeployment.config_version`/`last_sync_at`/`last_sync_status`.
- `GET /platform/organizations/{id}/sync-status` and
  `POST .../sync-status/retry` back the owner panel's pending/synced/failed
  view with a safe retry action (re-queues *current* state, not the old
  failed payload).

### Backend enforcement triad

Unchanged mechanism: `deps.py`'s `RequestOrgDep`/`CurrentOrgDep` already
call `enforce_org_license` on every request; `require_feature(feature)`
gates a router on allocated features; `require_role` gates on local
permissions. Stacking these three dependencies (as `admin.py` and friends
already do) is the triad — now backed by the direct-HTTPS-validation cache
in client mode, applying equally to API requests, existing sessions
(re-checked per request, not just at login), background jobs (workers read
the same `deps.py` helpers), and real-time operations (the voice/WhatsApp
channel code paths run through the same org-scoped dependencies).
`routers/provisioning.py`'s two endpoints (`/provisioning/license-status`,
`/provisioning/recheck`) are deliberately **not** behind this gate — they
must stay reachable during a licence outage so the client's own dashboard
can explain why it's blocked and offer a manual recheck.

**Platform-owner exemption**: `deps.py::_org_is_platform_admin_owned`
(unchanged) exempts any org with a superuser member from every licence/
feature check, on both owner and client deployments. In practice this only
ever matters on the owner deployment, since a client's own separate
database has no path to create a superuser account — an ordinary client
org admin cannot grant themselves this exemption because there is no
endpoint that sets `AccountUser.is_superuser`.

### Owner-panel connection status

`core/deployment_status.py::compute_connection_status` reduces the
technical state (`ClientDeployment.status`/`last_seen_at`/`config_version`
vs. `Org.config_version`) to exactly four owner-facing states, carried as
`OrganizationOut.connection_status`:

- `awaiting_client_setup` — a token exists (or, for a legacy org, none has
  even been prepared yet) but the deployment has never successfully
  authenticated.
- `connected` — has authenticated AND acknowledged the org's current
  configuration. **Never shown merely because a token was generated.**
- `sync_pending` — authenticated, but hasn't yet acknowledged the latest
  configuration version, or its last sync attempt failed.
- `connection_issue` — the token was revoked, or the deployment
  authenticated before but hasn't checked in for an unusually long time
  (three validation-cache cycles, ~15 minutes).

This is deliberately the ONLY status shown on the normal organization list/
form. The full technical detail (`ClientDeployment.status`, raw
`last_seen_at`/`last_sync_at`/`last_sync_error`, pending event count)
remains available via `GET /platform/organizations/{id}/sync-status` — a
separate "setup/details" view, not the everyday organization screen.

## 3. Database migrations

`migrations/versions/1fbd0c6f9ede_add_multi_tenant_licensing.py` (edited in
place during the simplification above, since it had not shipped to
production yet — no corrective follow-up migration was needed):
- Creates `client_deployments` (`token_hash` unique, plus status/sync
  bookkeeping columns), `license_audit_events`, `sync_events`.
- Adds `orgs.central_org_ref` (backfilled to `orgs.id` for every existing
  org, then made `NOT NULL UNIQUE`), `orgs.deployment_status` (backfilled
  to `"provisioned"` for existing orgs — they're already running),
  `orgs.config_version`, `orgs.client_deployment_id`.

Run on the owner deployment with the usual `alembic upgrade head` (same as
`deploy-aws.yml` already does on every deploy). **Not applied to production
as part of this change.**

## 4. Environment variables — one schema, one `.env.example`

There is exactly one config schema (`apps/api/config.py`'s `Settings`) and
one template file, **`.env.example`** at the repo root, used by the owner
deployment and every client deployment alike.

New fields (see `.env.example`'s own inline comments for the full picture
of every setting, not just the new ones):

- `DEPLOYMENT_MODE` — `owner` (default) or `client`.
- `LICENSE_API_URL` — client-only: the owner API's base URL.
- `LICENSE_API_TOKEN` — client-only: this deployment's bearer token, issued
  by `POST /platform/organizations/{id}/deployments`.

That's the entire new surface — no signing keys, no key ids, no issuer
string, no cache-lifetime variable (the 5-minute cache is a hardcoded
constant, `core/license_cache.py::CACHE_TTL_SECONDS`, deliberately not
environment-configurable), no offline-grace variable (there isn't one).

`Settings` validates these against `DEPLOYMENT_MODE` at startup
(`_validate_deployment_mode_requirements`): `DEPLOYMENT_MODE=client` refuses
to start unless both `LICENSE_API_URL` and `LICENSE_API_TOKEN` are set — a
client silently running with no way to validate its licence would either
crash on first use or, worse, run with no enforcement at all.
`DEPLOYMENT_MODE=owner` has no additional required fields; it validates
itself via the platform-owner exemption, not a client token.

## 5. Exact steps to update the existing owner deployment

1. `git pull` / redeploy as usual (`.github/workflows/deploy-aws.yml`
   already runs `alembic upgrade head` on every deploy — no change needed
   there).
2. Nothing else changes: `DEPLOYMENT_MODE` defaults to `"owner"`, every
   existing route/worker keeps running exactly as before, and every
   existing `Org` row is backfilled by the migration to
   `deployment_status="provisioned"` with `central_org_ref = id` — no
   behavior change for current customers. There is no signing key to
   generate or configure on the owner in this design.

## 6. Exact steps to provision and connect the first client

The only manual technical steps are hosting-side; the organization/
deployment/token side is one API call. See `infra/client-template/README.md`
for the full walkthrough; summary:

1. `POST /platform/organizations` on the owner API — one call creates the
   org, allocates its features, records an optional first licence, AND
   auto-registers its default deployment + generates its token. The
   response's `setup` object contains everything needed to configure the
   client (`central_org_ref`, `license_api_url`, `deployment_token`, and a
   ready-to-paste `env_snippet`) — shown exactly once. This step alone does
   NOT create any AWS resources; `deployment_status` starts
   `"pending_deployment"`.
2. Run `infra/client-template/provision_client.sh <slug> <api-domain>
   <web-domain>` — reviews/prints the DB + Caddy + docker-compose setup for
   the client's own AWS infrastructure (does not execute AWS calls itself).
3. Paste the `env_snippet` from step 1 into the client's `.env` (same
   `Settings` schema as the owner's own `.env`), filling in `DATABASE_URL`
   with the client's actual database connection from step 2. No other
   technical work is needed on the licensing/organization side.
4. `docker compose up -d --build && docker compose exec api alembic upgrade
   head` on the client's stack — migrations run once, here, before the
   application starts; workers never run competing migrations themselves.
5. Start the application. The client's `central_sync_worker` authenticates,
   retrieves its assigned org/licence/features, creates or updates the
   local organization idempotently, and acknowledges the applied
   configuration — all automatically. If the owner API is unreachable at
   this point, the app stays in a restricted "setup pending" state (no
   business access) and retries automatically on a short backoff until it
   succeeds — see `workers/central_sync_worker.py`'s
   `_FIRST_SUCCESS_RETRY_SECONDS`.
6. Confirm `GET /platform/organizations/{id}/sync-status` on the owner
   shows `status: "active"`, and the org's `connection_status` (from `GET
   /platform/organizations`) reads `"connected"` — meaning the client has
   both authenticated AND acknowledged its configuration, not merely that a
   token exists.

**If the setup token is lost** before the client ever applies it: use
`POST /platform/organizations/{id}/deployments/regenerate-token` (owner-
only) to get a fresh one. This invalidates the previous token immediately;
the client's `.env` must be updated and the app restarted. This is never
part of ordinary renewals, feature updates, or restarts — none of those
touch the token.

**For an organization that predates this feature** (no deployment yet):
`POST /platform/organizations/{id}/deployments` prepares one without
touching any business data, returning the same one-time setup payload as
step 1 above.

## 7. Test results

Ran locally against this repo's existing SQLite-in-memory test convention
(`apps/api/tests/conftest.py`), `ADMIN_TOKEN=test`. Tests were rewritten
alongside the simplification (the earlier JWT-assertion tests no longer
apply — there is no signature to test):

- `apps/api/tests/test_client_license_enforcement.py` (6 tests) — no cached
  result blocks access, active result allows it, a suspended cached result
  blocks access even before its own expiry, an expired cached result blocks
  access, feature restriction/unrestriction from the cached result.
- `apps/api/tests/test_license_cache.py` (10 tests) — successful refresh
  caches the result; cache expiry never exceeds the licence's own actual
  expiry; a failed refresh leaves the existing cache completely untouched
  (neither extended nor cleared) and never resurrects an already-expired
  cache; a successful but unfavorable refresh (suspended/revoked)
  immediately overwrites a favorable cached entry; malformed responses and
  organization-identity mismatches are treated as failures, never granting
  access; non-2xx responses are treated as failures; concurrent `refresh()`
  calls coalesce onto a single outbound HTTP request.
- `apps/api/tests/test_deployment_auth.py` (8 tests) — missing/malformed/
  unknown/cross-client bearer tokens; a valid token scopes strictly to its
  own org; a revoked token is rejected even though it still matches; a
  reissued token invalidates the old one immediately; the raw token is
  never echoed back by any read endpoint and the database only ever holds
  its hash.
- `apps/api/tests/test_platform_provisioning.py` (14 tests, after the
  one-click onboarding change) — one-click organization creation
  auto-registering its deployment + token (with `Cache-Control: no-store`
  and the exact `env_snippet` content asserted); duplicate submission with
  the same `idempotency_key` returns the existing org without a second
  deployment/token; the same race genuinely concurrent at the database
  level (see below) recovers via the unique constraint instead of creating
  a duplicate; full licence audit trail (issue/suspend/revoke/reactivate);
  `connection_status` progressing `awaiting_client_setup` →
  `sync_pending` → `connected` through an actual authenticate-then-ack
  sequence (never "connected" merely because a token exists); a revoked
  deployment shows `connection_issue`; "prepare setup" for an organization
  without a deployment leaves its business data untouched and refuses a
  second preparation; pending-sync + ack reflecting current org state;
  a feature update requires no new token/registration; idempotent +
  out-of-order provisioning application (first apply, duplicate retry,
  newer version, stale version all resolve correctly); direct API bypass
  attempts with no/ordinary-session credentials; the platform-admin-vs-
  ordinary-client-admin restriction; the platform-owner exemption bypassing
  licence checks entirely with no licence issued at all.
- `apps/api/tests/test_deployment_auth.py` (8 tests, updated for one-click
  creation) — missing/malformed/unknown/cross-client bearer tokens; a valid
  token scopes strictly to its own org; a revoked token is rejected even
  though it still matches; a reissued token invalidates the old one
  immediately; the raw token is never echoed back by any read endpoint and
  the database only ever holds its hash.
- `apps/api/tests/test_config_deployment_mode.py` (4 tests) — mode-
  conditional required configuration.
- Full existing suite (`apps/api/tests/`, 550+ tests): re-run after both the
  direct-HTTPS simplification and the one-click onboarding change — all
  passing except one pre-existing, unrelated failure
  (`test_leads_csv_includes_channel_column`, expecting a CSV header without
  a "staff" column added by an earlier, unrelated commit) that predates
  this feature entirely.

### A genuine concurrency bug caught and fixed during testing

The first version of `core/provisioning_apply.py`'s race-recovery (catch the
losing INSERT's `IntegrityError`, re-read and update the winner's row
instead) looked correct but was initially tested against this suite's
shared in-memory SQLite fixture, which routes every session through ONE
pooled connection (`StaticPool`, required for schema/data visibility across
sessions on `:memory:`). That makes two "concurrent" sessions share one
actual transaction context, so one session's `rollback()` silently wiped
out the other's uncommitted insert — not a bug in the recovery logic
itself, but a false negative in the original test. Re-tested against a real
on-disk SQLite file (genuinely separate connections/transactions per
session, much closer to how two separate Postgres connections behave in
production) confirmed the recovery logic is correct: exactly one row exists
after two concurrent first-time applies of the same organization. See
`test_concurrent_first_start_does_not_create_duplicate_local_org`'s
docstring for the full explanation. A parallel HTTP-level "concurrent
duplicate organization creation" test was attempted but is not meaningful
under this suite's `client` fixture for the same reason (it shares one
session across concurrent requests) — that fixture limitation is
documented in the test file rather than worked around, since building a
true multi-connection HTTP test harness wasn't justified for this one case.

### Explicitly NOT verified (require a real deployed environment)

- Any real AWS resource creation, RDS/database isolation under load, Caddy
  vhost routing, or actual multi-host network reachability —
  `provision_client.sh` is reviewed and documented, not executed.
- A real second physical deployment actually exchanging validation/sync
  traffic over a network (tests exercise both roles' code paths in-process
  against SQLite and a mocked HTTP client, not two live services).
- Refresh coordination across multiple real replicas of the same client
  deployment (the jitter/coalescing logic is implemented and unit-testable
  within one process, but multi-process race behavior needs a real
  multi-replica environment to observe).
- Rate limiting under real concurrent/adversarial load (slowapi's in-memory
  limiter is wired onto `GET /platform/license` and
  `POST /provisioning/recheck`; its behavior under genuine load, and
  whether an in-memory limiter is sufficient once the owner runs more than
  one process, is not verified here).
- mypy/type-check pass on the new modules — `mypy` isn't installed in this
  environment; `python -m py_compile`/`ast.parse` and importing the app in
  both `DEPLOYMENT_MODE=owner` and `=client` confirmed correctness at that
  level.
- The first-start "setup pending, retry automatically" behavior
  (`workers/central_sync_worker.py`'s short-backoff loop before the first
  successful validation) is implemented and its backoff sequence is plain
  code, but has not been exercised against a genuinely unreachable owner API
  over a real network — only via `core/license_cache.py`'s unit tests, which
  mock the HTTP layer directly.
- Restarting an already-provisioned client and confirming it does NOT
  create a duplicate local organization was verified via direct calls to
  `apply_provisioning_locally` (idempotent on `central_org_ref`), not via
  an actual process restart of a running client deployment.

## 7a. Frontend — not built in this pass

Everything in §2 ("From the owner panel...") is implemented as backend API
only (`routers/platform.py`) in this change — including the one-click
organization creation, connection-status field, and one-time setup screen
content (`SetupInstructionsOut`). The existing
`apps/web/src/app/(dashboard)/organizations/` page and `useAdminOrgs.ts`
hooks still only cover the pre-existing `/billing/orgs` licence management
UI; there is no actual "Create organization" button/dialog, no setup-screen
modal showing the one-time token/env-snippet, and no
awaiting/connected/sync-pending/connection-issue status pills in the
owner-facing UI. Building these on top of the new `/platform/*` endpoints
is straightforward (same `apiFetch`/React Query hook pattern already used
by `useAdminOrgs.ts`) but was not done here — the backend contract is
designed for exactly this UI (a single POST returns everything a "Save"
button + a one-time setup modal need), but the UI itself is unbuilt.

## 8. Known limitations / operational notes

- **This design assumes Veerox controls the deployed client backends.** It
  does not, and cannot, prevent someone with root/deploy access to a
  client's own server from modifying that server's copy of the application
  to bypass enforcement locally. The security boundary is the owner API and
  its database — not tamper-resistance of code running on hardware a client
  operator controls. If a client's infrastructure is ever operated by
  someone Veerox doesn't fully control, this model needs re-evaluating.
- `/billing/social-links` (a normal-customer-readable endpoint, not
  platform-admin-only) currently lives inside `billing.router`, which is
  excluded entirely from client deployments. A client dashboard's social
  links feature is therefore unavailable until that one read-only route is
  split into its own always-mounted router.
- Redis isolation between clients sharing one ElastiCache instance is
  namespace-only (logical DB index), not access-control isolation — see
  `infra/client-template/README.md`.
- `provision_client.sh` is a reviewed runbook, not idempotent
  infrastructure-as-code — there is no Terraform/CDK/Pulumi in this repo
  today.
- A confirmed suspension/revocation can take up to the fixed 5-minute cache
  lifetime to reach an online client — use the rate-limited
  `POST /provisioning/recheck` for prompt renewal instead of waiting it out.
  If the owner API is unreachable, there is **no offline grace** — once the
  cache expires, protected access blocks outright until validation succeeds.
- Shared-RDS-instance cost for a client is genuinely shared infrastructure
  cost, not a precise per-client bill — do not quote it as one.
- The deployment token is never stored in a recoverable form and is never
  redisplayed after the initial setup response. If it's lost before the
  client applies it, the only recovery is
  `POST .../deployments/regenerate-token`, which invalidates the old one —
  there is no "show me the token again" endpoint by design.
- `connection_status`'s `connection_issue` bucket covers two different
  underlying causes (a revoked token, or a stale/silent deployment) with the
  same label — the detailed view (`GET .../sync-status`) distinguishes them
  via `status`/`last_seen_at`, but the simple owner-facing status does not.

## 9. Remaining actions requiring production approval

- Applying `1fbd0c6f9ede_add_multi_tenant_licensing` to the production
  database.
- Any actual AWS resource creation for a first client (RDS
  database/instance, EC2 host changes, Caddy config reload, Secrets
  Manager entries) — `provision_client.sh` prints these, it does not run
  them.
- Choosing and creating the actual first client to onboard, and generating/
  distributing its `LICENSE_API_TOKEN` out-of-band (e.g. a secure password
  manager entry, never email/Slack in plaintext).
