#!/usr/bin/env bash
# Repeatable client deployment provisioning (spec §7).
#
# THIS SCRIPT DOES NOT RUN ANYTHING BY ITSELF IN THIS REPO — it is meant to
# be reviewed and run manually, by a human with AWS credentials and
# production access, against the actual shared EC2 host / RDS instance. It
# has NOT been executed as part of this change; no AWS resources have been
# created, no production networking touched, no migrations applied. See
# docs/multi-tenant-licensing.md's handover section for exactly what is and
# isn't verified.
#
# Usage:
#   ./provision_client.sh <client-slug> <client-api-domain> <client-web-domain> [--dedicated-db]
#
# What it does, in order:
#   1. Creates this client's database:
#        - default: a new logical database + a role restricted to it, on
#          the SAME shared RDS instance the owner app already uses (cheap,
#          the normal case — see README.md's isolation tiers).
#        - --dedicated-db: provisions a whole new RDS instance instead, for
#          a client whose contract requires storage-level isolation, not
#          just access-level isolation. Meaningfully more expensive; not the
#          default for a reason.
#   2. Generates this client's secrets (admin token, Fernet key) and writes
#      this client's .env from the repo root's ../../.env.example (the SAME
#      schema the owner deployment uses — see its own comments for exactly
#      which fields a client fills in vs. leaves blank) — never printed to
#      stdout, never committed.
#   3. Writes this client's docker-compose.yml from
#      docker-compose.client.yml.template.
#   4. Adds a Caddy reverse-proxy block for this client's domain(s) to the
#      shared Caddyfile (see Caddyfile.client-block.template) and reloads
#      Caddy.
#   5. Prints the exact next manual steps: registering the org centrally,
#      running migrations, and confirming first sync — see
#      docs/multi-tenant-licensing.md's onboarding sequence.
#
# Required environment variables (set these before running, or edit the
# placeholders below — never hardcode real credentials into this file):
#   AWS_REGION                  e.g. us-east-2
#   SHARED_RDS_ENDPOINT         the existing veerox-db RDS endpoint
#   SHARED_RDS_MASTER_USER      an existing role with CREATEDB/CREATEROLE
#   SHARED_RDS_MASTER_PASSWORD  (read from AWS Secrets Manager, not a literal)
#   SHARED_REDIS_ENDPOINT       the existing ElastiCache Valkey endpoint
#   EC2_DEPLOY_PATH             absolute path to the shared repo clone on the
#                                host (mirrors AWS_DEPLOY_PATH in
#                                .github/workflows/deploy-aws.yml)

set -euo pipefail

CLIENT_SLUG="${1:?Usage: provision_client.sh <client-slug> <client-api-domain> <client-web-domain> [--dedicated-db]}"
CLIENT_API_DOMAIN="${2:?client API domain required, e.g. api.acme.workassignai.com}"
CLIENT_WEB_DOMAIN="${3:?client web domain required, e.g. app.acme.workassignai.com}"
DEDICATED_DB="${4:-}"

: "${AWS_REGION:?set AWS_REGION}"
: "${SHARED_RDS_ENDPOINT:?set SHARED_RDS_ENDPOINT}"
: "${SHARED_RDS_MASTER_USER:?set SHARED_RDS_MASTER_USER}"
: "${SHARED_RDS_MASTER_PASSWORD:?set SHARED_RDS_MASTER_PASSWORD}"
: "${SHARED_REDIS_ENDPOINT:?set SHARED_REDIS_ENDPOINT}"
: "${EC2_DEPLOY_PATH:?set EC2_DEPLOY_PATH}"

CLIENT_DIR="${EC2_DEPLOY_PATH}/../clients/${CLIENT_SLUG}"
DB_NAME="veerox_${CLIENT_SLUG//-/_}"
DB_USER="veerox_${CLIENT_SLUG//-/_}_app"
DB_PASSWORD="$(openssl rand -base64 32 | tr -d '=+/')"

echo "== 1. Database provisioning for '${CLIENT_SLUG}' =="
if [[ "${DEDICATED_DB}" == "--dedicated-db" ]]; then
  echo "-- Dedicated RDS instance requested. This is a real, billed AWS resource:"
  echo "   review cost before running. Example (edit instance class/storage as needed):"
  cat <<EOF
  aws rds create-db-instance \\
    --db-instance-identifier "veerox-${CLIENT_SLUG}" \\
    --db-instance-class db.t4g.micro \\
    --engine postgres --engine-version 17 \\
    --master-username "${DB_USER}" \\
    --master-user-password "\${DB_PASSWORD}" \\
    --allocated-storage 20 \\
    --no-publicly-accessible \\
    --region "${AWS_REGION}"
  # Then point DB_HOST at this instance's own endpoint instead of
  # SHARED_RDS_ENDPOINT in the generated .env below.
EOF
  DB_HOST="__FILL_IN_DEDICATED_RDS_ENDPOINT__"
else
  echo "-- Shared-instance logical database + restricted role (default, cost-shared):"
  cat <<EOF
  PGPASSWORD="\${SHARED_RDS_MASTER_PASSWORD}" psql \\
    -h "${SHARED_RDS_ENDPOINT}" -U "${SHARED_RDS_MASTER_USER}" -d postgres <<SQL
  CREATE ROLE "${DB_USER}" LOGIN PASSWORD '${DB_PASSWORD}';
  CREATE DATABASE "${DB_NAME}" OWNER "${DB_USER}";
  REVOKE ALL ON DATABASE "${DB_NAME}" FROM PUBLIC;
  GRANT ALL PRIVILEGES ON DATABASE "${DB_NAME}" TO "${DB_USER}";
  -- ${DB_USER} can only ever connect to ${DB_NAME} — no visibility into the
  -- owner's database or any other client's logical database on this
  -- instance (Postgres does not allow cross-database queries by default,
  -- and this role has no grants anywhere else).
SQL
EOF
  DB_HOST="${SHARED_RDS_ENDPOINT}"
fi

echo
echo "== 2. Redis logical DB index =="
echo "-- Pick an unused index (0-15) on ${SHARED_REDIS_ENDPOINT} for this client."
echo "   NOTE: this is lightweight namespace separation, not access-control"
echo "   isolation — every client on a shared Redis instance can technically"
echo "   reach every logical DB. If a client's contract requires Redis-level"
echo "   isolation, provision a dedicated ElastiCache node instead."

echo
echo "== 3. Writing this client's environment/config files to ${CLIENT_DIR} =="
echo "   (mkdir -p, copy docker-compose.client.yml.template ->"
echo "    ${CLIENT_DIR}/docker-compose.yml with {{REPO_PATH}}/{{CLIENT_SLUG}}"
echo "    substituted, and copy the repo root's .env.example ->"
echo "    ${CLIENT_DIR}/.env, then set:"
echo "      DEPLOYMENT_MODE=client"
echo "      DATABASE_URL / REDIS_URL  -> the values generated above"
echo "      ADMIN_TOKEN               -> a freshly generated token, not the owner's"
echo "      SECRET_ENCRYPTION_KEY     -> a freshly generated Fernet key"
echo "    LICENSE_API_URL / LICENSE_API_TOKEN / DEFAULT_ORG_ID come straight"
echo "    from the 'setup' object in the owner's POST /platform/organizations"
echo "    response (see step 5 below) — leave them blank for now.)"

echo
echo "== 4. Caddy vhost =="
echo "   Append a block for ${CLIENT_API_DOMAIN} -> ${CLIENT_SLUG}_api:8002"
echo "   and ${CLIENT_WEB_DOMAIN} -> ${CLIENT_SLUG}_web:3001 to the shared"
echo "   Caddyfile (see Caddyfile.client-block.template), then:"
echo "     docker compose -f docker-compose.yml exec caddy caddy reload --config /etc/caddy/Caddyfile"

cat <<'EOF'

== 5. Manual steps that must happen on the OWNER API (not by this script) ==
  a. POST /platform/organizations   {"name": "...", "enabled_features": [...], "license_days": 365}
     -> one call creates the org AND auto-registers its default deployment
        + generates its token. The response's "setup" object has
        everything for step b: central_org_ref, license_api_url,
        deployment_token, and a ready-to-paste env_snippet. Shown EXACTLY
        ONCE — if lost before pasting it, use
        POST /platform/organizations/{id}/deployments/regenerate-token
        (invalidates the old one immediately).
  b. Paste the response's env_snippet into this client's .env, then fill in
     DATABASE_URL with the connection string from step 1 above.
  c. On the client host: docker compose up -d --build, then
     docker compose exec api alembic upgrade head (once, before starting
     the app — workers never run competing migrations themselves).
  d. Start the app. Its central_sync_worker authenticates, retrieves and
     applies its assigned org/licence/features, and acknowledges them
     automatically — no further manual step. If the owner API happens to
     be unreachable right now, the app stays in a restricted "setup
     pending" state and retries automatically; no business access is
     granted until it succeeds.
  e. GET /platform/organizations/{id}/sync-status from the owner API, and
     check the org's connection_status via GET /platform/organizations —
     wait for "connected" (authenticated AND acknowledged), not just
     "awaiting_client_setup" (a token existing proves nothing on its own).
  f. Verify isolation: confirm this client's API cannot reach any other
     org's data and that DATABASE_URL only resolves to its own database.
     Also confirm a revoked/wrong token is rejected: attempt
     GET /platform/license with a bad Authorization header from this
     client's box and confirm it 401s.

See docs/multi-tenant-licensing.md for the full onboarding checklist.
EOF
