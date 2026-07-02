# Veerox AI — Voice + WhatsApp Agent

Veerox AI is a multi-channel conversational agent that handles inbound voice calls (via Plivo + OpenAI Realtime API) and WhatsApp messages (via Meta Cloud API). It captures leads, books appointments, and escalates to a human operator — all backed by a FastAPI + PostgreSQL + Redis stack and managed through a Next.js admin dashboard.

For architecture decisions, data-flow diagrams, and the full build plan, see [`longrunning/README.md`](longrunning/README.md).

---

## Quick start

```bash
cp .env.example .env
# Fill in OPENAI_API_KEY, PLIVO_*, META_*, and ADMIN_TOKEN

./scripts/dev.sh          # boots Postgres + Redis, runs migrations, starts FastAPI on :8000

# In a separate terminal — admin dashboard on :3000
cd apps/web && npm install && npm run dev
```

> **Windows users:** run `scripts/dev.sh` inside Git Bash or WSL.
> Make the script executable once with: `git update-index --chmod=+x scripts/dev.sh`

---

## Repo layout

```
veerox-core/
├── apps/
│   ├── api/               # FastAPI backend
│   │   ├── core/          # AgentCore, tools, memory, LLM wrapper
│   │   ├── channels/      # WhatsApp + voice webhook handlers (stubs)
│   │   ├── db/            # SQLAlchemy models + session
│   │   ├── routers/       # HTTP route handlers
│   │   ├── schemas/       # Pydantic v2 schemas
│   │   └── tests/         # pytest test suite
│   └── web/               # Next.js 14 admin dashboard
├── migrations/            # Alembic revision files
├── scripts/
│   ├── dev.sh             # local dev bootstrap
│   └── seed.py            # inserts demo org + user
├── compose.yml            # Postgres + Redis + API
├── Dockerfile             # multi-stage Python image
├── pyproject.toml         # uv deps + ruff / mypy / pytest config
├── alembic.ini            # Alembic config (url read from env)
└── .env.example           # all required env var keys (no values)
```

---

## How to add a tool to AgentCore

Tool definitions live in [`apps/api/core/tools.py`](apps/api/core/tools.py).

1. Add an OpenAI-format tool schema to `TOOL_DEFINITIONS`.
2. Add the corresponding async handler function.
3. Register it in the dispatch table at the bottom of the file.

AgentCore in [`apps/api/core/agent.py`](apps/api/core/agent.py) calls the dispatch table automatically when the model returns a tool-call.

---

## How to swap LLM provider

The entire LLM interaction is isolated in [`apps/api/core/llm.py`](apps/api/core/llm.py). It exposes a single async callable that AgentCore uses. To swap providers:

1. Replace the implementation in `llm.py` to call your preferred API.
2. Update the relevant env vars in `.env.example` and `apps/api/config.py`.
3. Nothing else changes — AgentCore is provider-agnostic.

---

## Adding a migration

```bash
# After editing/adding a SQLAlchemy model in apps/api/db/models/:
uv run alembic revision --autogenerate -m "describe what changed"

# Apply to the local database:
uv run alembic upgrade head
```

Alembic reads `DATABASE_URL` from your `.env` file at runtime (wired in `migrations/env.py`).

---

## Running tests / lint / typecheck

```bash
# Tests
uv run pytest

# Linter + formatter check
uv run ruff check apps/
uv run ruff format --check apps/

# Type checking (strict on the agent core)
uv run mypy apps/api/core
```

---

## Where logs go

The backend emits **structured JSON logs** to stdout via `structlog`. Every log line is machine-readable and ships cleanly to any aggregator (CloudWatch, Datadog, Loki, etc.) — just point your collector at the container's stdout.

For error tracking, set `SENTRY_DSN` in your `.env` (or AWS SSM in production). If the variable is absent, Sentry is a no-op — nothing breaks.
