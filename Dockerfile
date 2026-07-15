# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Stage 1: builder
#   - Installs uv, resolves & installs runtime deps into /app/.venv
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Copy dependency manifest (and lockfile if it exists)
COPY pyproject.toml ./
# uv.lock is optional at scaffold time — copy only when present
COPY uv.lock* ./

# Sync runtime deps into an isolated venv.
# --mount=type=cache speeds up rebuilds when BuildKit is available.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --python python3.12 || \
    uv sync --no-dev --python python3.12

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Create a non-root user
RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

# Pull the fully-resolved venv from the builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY apps/ ./apps/
COPY migrations/ ./migrations/
COPY alembic.ini ./alembic.ini

# Make sure the app user owns everything
RUN chown -R app:app /app

USER app

# Add venv binaries to PATH
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8001

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8001"]
