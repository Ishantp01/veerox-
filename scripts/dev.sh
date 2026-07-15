#!/usr/bin/env sh
# scripts/dev.sh — boot local dev environment
#
# Usage:  ./scripts/dev.sh
#
# NOTE FOR WINDOWS USERS: Git Bash or WSL is required to execute this script.
# The file should be made executable before use:
#   git update-index --chmod=+x scripts/dev.sh   (records +x in the git index on any OS)
# or on Linux/macOS:
#   chmod +x scripts/dev.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# 1. Start Postgres and Redis in the background
# ---------------------------------------------------------------------------
echo "==> Starting db and redis..."
docker compose up -d db redis

# ---------------------------------------------------------------------------
# 2. Wait for Postgres to be ready (max 30 seconds)
# ---------------------------------------------------------------------------
echo "==> Waiting for Postgres to be ready..."
RETRIES=0
MAX_RETRIES=30

until docker compose exec -T db pg_isready -U veerox > /dev/null 2>&1; do
    RETRIES=$((RETRIES + 1))
    if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
        echo "ERROR: Postgres did not become ready within ${MAX_RETRIES}s. Check 'docker compose logs db'."
        exit 1
    fi
    printf "  waiting... (%ds)\n" "$RETRIES"
    sleep 1
done
echo "==> Postgres is ready."

# ---------------------------------------------------------------------------
# 3. Run Alembic migrations
# ---------------------------------------------------------------------------
echo "==> Running migrations..."
if uv run alembic upgrade head 2>&1; then
    echo "==> Migrations applied (or already up to date)."
else
    echo "NOTE: Alembic upgrade returned non-zero. This is expected if no migration" \
         "files exist yet. Run 'uv run alembic revision --autogenerate -m \"initial\"'" \
         "to create your first migration, then re-run this script."
fi

# ---------------------------------------------------------------------------
# 4. Start FastAPI with hot-reload (replaces this shell process via exec)
# ---------------------------------------------------------------------------
echo "==> Starting FastAPI on http://0.0.0.0:8001 ..."
exec uv run uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8001
