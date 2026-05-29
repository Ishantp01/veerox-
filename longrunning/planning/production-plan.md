# Veerox AI — Project Structure & Production-Ready Plan

_Last updated: 2026-05-29_

---

## 1. What this project is (in one paragraph)

**Veerox AI** is a multi-channel AI conversational agent for businesses. A customer can either
**call a phone number** (voice) or **send a WhatsApp message**, and an AI agent (OpenAI GPT-4o /
Realtime) answers in natural language — in English *or* Hindi. The agent can **capture leads**,
**book appointments**, **look up existing customers**, and **escalate to a human** when needed.
Everything is observable and controllable from a **Next.js admin dashboard** (live transcripts,
lead inbox, escalations, cost tracking, and a kill-switch to pause the agent).

The guiding architecture principle: **one brain, two mouths** — a single channel-agnostic
`AgentCore` handles all reasoning, and voice/WhatsApp are thin transport adapters around it.

---

## 2. The business flow (what actually happens)

```
Customer                         Veerox AI                          Business
   │                                 │                                  │
   │  "Hi, I want a gym package"     │                                  │
   ├────────── WhatsApp/Call ───────▶│                                  │
   │                                 │ 1. Understand intent (GPT-4o)    │
   │                                 │ 2. Ask name + phone              │
   │◀──── "Sure! Your name?" ────────┤                                  │
   │                                 │ 3. capture_lead() → DB           │
   │  "Priya, 98765 43210"           │                                  │
   ├────────────────────────────────▶│ 4. book_appointment() / escalate│
   │◀── "Booked for tomorrow 4pm" ───┤                                  │
   │                                 │ 5. Lead + transcript persisted   │
   │                                 ├──────── appears on dashboard ───▶│ Operator
   │                                 │                                  │ sees lead,
   │                                 │                                  │ can take over
```

The four agent **tools**: `capture_lead`, `book_appointment`, `transfer_to_human`,
`lookup_customer` (defined in `apps/api/core/tools.py`).

---

## 3. Tech stack

| Layer | Technology | Why |
|---|---|---|
| Backend API | **FastAPI** (Python 3.12, async) | Async-native, great for webhooks + WebSockets |
| AI — chat | **OpenAI GPT-4o** | Strong Hindi/English, tool calling |
| AI — voice | **OpenAI Realtime API** (`gpt-4o-realtime`) | Native speech-to-speech, low latency |
| Transcription | **OpenAI Whisper** | WhatsApp voice notes → text |
| Telephony | **Twilio** (Voice + Media Streams) | Phone numbers + audio WebSocket |
| WhatsApp | **Meta Cloud API** (direct, no BSP) | Lower cost, full control |
| Database | **PostgreSQL 16** (Neon in dev) + SQLAlchemy async + Alembic | Relational data, multi-tenant ready |
| Cache / state | **Redis** | Kill-switch, rate limits, handoff queue, idempotency |
| Frontend | **Next.js 14** + Tailwind + Lucide icons | Admin control plane |
| Package mgmt | **uv** (Python), **npm** (web) | Fast, reproducible |
| Observability | **structlog** (JSON logs) + **Sentry** | Cloud-agnostic logging + error tracking |
| Deploy | **Docker** + **Caddy** (auto-TLS) on EC2 (`ap-south-1`) | Simple single-box prod |

---

## 4. Repository structure

```
veerox-core/
├── apps/
│   ├── api/                          # FastAPI backend
│   │   ├── core/                     # 🧠 The brain (channel-agnostic)
│   │   │   ├── agent.py              # AgentCore.handle_turn() — the agent loop   ✅
│   │   │   ├── llm.py                # OpenAI chat wrapper (swap providers here)  ✅
│   │   │   ├── tools.py              # 4 tool definitions + handlers + dispatch   ✅
│   │   │   ├── memory.py             # load/persist conversation history          ✅
│   │   │   ├── prompts.py            # base prompt + per-channel append blocks    ✅
│   │   │   ├── transcribe.py         # Whisper wrapper for voice notes            ✅
│   │   │   └── costs.py              # model pricing constants                    ❌ TODO
│   │   ├── channels/                 # 📞 Thin transport adapters
│   │   │   ├── whatsapp/
│   │   │   │   ├── webhook.py        # Meta verify (GET) + receipt (POST)         ✅
│   │   │   │   ├── adapter.py        # Meta payload ↔ handle_turn                 ✅
│   │   │   │   └── client.py         # send_text / download_media / mark_read     ✅
│   │   │   └── voice/
│   │   │       ├── twilio_webhook.py # returns TwiML <Connect><Stream>            ❌ STUB
│   │   │       ├── realtime_bridge.py# Twilio audio ↔ OpenAI Realtime WS          ❌ STUB
│   │   │       └── adapter.py        # Realtime tool-call bridging                ❌ STUB
│   │   ├── db/
│   │   │   ├── models/               # users, conversations, messages, leads, org ✅
│   │   │   ├── base.py / session.py  # async engine + session factory             ✅
│   │   ├── routers/
│   │   │   ├── admin.py              # stats, prompts, tools, kill-switch, etc.    ✅
│   │   │   ├── conversations.py      # GET conversations + messages               ✅
│   │   │   ├── leads.py              # GET leads (+ CSV)                           ✅
│   │   │   ├── health.py             # /health + /ready                           ✅
│   │   │   └── calls.py              # POST /calls/initiate (outbound dial)        ❌ TODO
│   │   ├── schemas/                  # Pydantic v2 request/response models         ✅
│   │   ├── tests/                    # pytest suite (agent, tools, memory, WA)     ✅
│   │   ├── config.py                 # settings from env                          ✅
│   │   ├── deps.py                   # FastAPI dependencies (auth, db)            ✅
│   │   ├── rate_limit.py             # slowapi limiter                            ✅
│   │   ├── redis_client.py           # Redis connection                          ✅
│   │   ├── logging.py / sentry.py    # observability                             ✅
│   │   └── main.py                   # app factory + router mounting             ✅
│   └── web/                          # Next.js 14 admin dashboard
│       └── src/app/                  # dashboard, conversations, leads,
│                                     #   escalations, dial, settings, login      ✅
├── migrations/                       # Alembic versions                          ✅
├── scripts/  (dev.sh, seed.py)       # local bootstrap + demo data               ✅
├── compose.yml / Dockerfile          # containerization                         ✅
├── pyproject.toml / uv.lock          # Python deps + ruff/mypy/pytest            ✅
└── .env                              # secrets (gitignored)                      ✅
```

**Legend:** ✅ implemented · ❌ stub/missing

---

## 5. Current status — honest assessment

| Capability | Status | Notes |
|---|---|---|
| Agent brain (GPT-4o + tools + memory) | ✅ **Working** | Fully implemented |
| WhatsApp channel (in/out, voice notes) | ✅ **Working** (needs Meta creds) | Code done; `.env` keys empty |
| Admin dashboard (all pages) | ✅ **Working** | Polished UI, runs on :3001 |
| Database + migrations + Redis | ✅ **Working** | Neon PG + portable Redis |
| Admin API (stats, kill-switch, escalations) | ✅ **Working** | 452 lines, complete |
| **Voice channel (Twilio + Realtime)** | ❌ **Not built** | 3 stub files — the big gap |
| Outbound dial (`/calls/initiate`) | ❌ **Missing** | Dial page exists, backend route absent |
| Cost tracking (`costs.py`, USD spend) | ⚠️ **Partial** | Stat field exists, pricing constants missing |
| Production deployment | ❌ **Not done** | Runs locally only |
| Automated CI/CD | ❌ **Not done** | Tests exist, no pipeline |

**Bottom line:** WhatsApp side is production-grade code waiting on credentials. **Voice is the
main unbuilt feature.** Everything else is hardening + deployment.

---

## 6. Production-ready plan

Phased so each phase ends with something demonstrable.

### Phase A — Finish the feature set (1 week)

| # | Task | Files |
|---|---|---|
| A1 | Build **voice TwiML webhook** | `channels/voice/twilio_webhook.py` |
| A2 | Build **Realtime audio bridge** (the WebSocket dance + interruption handling via Twilio `mark`/`clear` events) | `channels/voice/realtime_bridge.py` |
| A3 | Build **voice tool-call bridging** (reuse same `DISPATCH_TABLE`) | `channels/voice/adapter.py` |
| A4 | **Stream voice transcripts to DB during the call** (sessions are ephemeral) | `realtime_bridge.py` |
| A5 | Build **outbound dial** endpoint | `routers/calls.py` + mount in `main.py` |
| A6 | Add **pricing constants + USD spend** calc | `core/costs.py` |

**Acceptance:** Call the Twilio number → English/Hindi conversation → `capture_lead` fires →
interrupting mid-sentence stops playback in ~200ms → transcript rows appear *during* the call.

### Phase B — Credentials & live integration (parallel, lead-time heavy)

| # | Task |
|---|---|
| B1 | OpenAI org: confirm `gpt-4o` **and** `gpt-4o-realtime` enabled |
| B2 | Twilio: buy/port India number (KYC 1–3 days), set Voice webhook |
| B3 | Meta: verify business, create WA app, get permanent token + Phone Number ID |
| B4 | Submit 2 WhatsApp utility templates (approval 24–48h, expect 1 rejection) |
| B5 | Register webhooks (WA + Twilio) against a stable HTTPS domain |
| B6 | Fill all empty keys in `.env` (Twilio + Meta) |

### Phase C — Production hardening (1 week)

| # | Task | Why |
|---|---|---|
| C1 | Secrets → **AWS SSM Parameter Store** (read at startup) | No secrets in `.env` on the box |
| C2 | **Caddy** reverse proxy + auto-TLS for `api.<domain>` | Meta/Twilio require HTTPS |
| C3 | Enable **webhook signature validation** for both Meta and Twilio | Prevent spoofed requests |
| C4 | **Rate limiting** per channel + per user phone (Redis INCR) | Abuse / runaway cost guard |
| C5 | **Graceful LLM failure** fallbacks (chat + Realtime) | Never crash on OpenAI 5xx |
| C6 | **Idempotency** on WhatsApp message IDs (Redis SETNX 24h) | Meta retries → no double replies |
| C7 | **Sentry DSN** populated + tested with a forced exception | Error visibility |
| C8 | `uvicorn --workers 2` behind Caddy (voice WS pins a worker) | Concurrency |
| C9 | Nightly **`pg_dump` → S3** backup cron | Disaster recovery |
| C10 | **Per-call cost counter** (audio seconds) into DB | Catch stuck sessions in $ |

### Phase D — Ops, CI/CD & observability (3–4 days)

| # | Task |
|---|---|
| D1 | **GitHub Actions**: `ruff` + `mypy` + `pytest` on every PR |
| D2 | **Docker build + push** to registry on merge to main |
| D3 | Deploy step (SSH/compose pull) or simple `watchtower` |
| D4 | **Health checks**: `/health` + `/ready` wired to uptime monitor |
| D5 | Dashboard for **cost + latency** (already has stat cards — feed real data) |
| D6 | **Log aggregation**: ship stdout JSON to CloudWatch/Loki |
| D7 | **Runbook**: how to rotate keys, pause the agent, restore backup |

### Phase E — Scale & Phase-2 features (later, explicitly out of MVP scope)

- Multi-tenant org switching (schema already supports `org_id`)
- Concurrency/load testing (10 calls, 100 WA users)
- Call recording playback in dashboard
- Knowledge-base RAG (`pgvector`)
- Move from `BackgroundTasks` → `arq`/Celery for durable jobs
- Real auth (OAuth/SSO) replacing the static admin token
- Browser-based voice (LiveKit)

---

## 7. Production readiness checklist (the gate)

Security
- [ ] Secrets in SSM, not in repo or plaintext on disk
- [ ] Both webhook signatures validated
- [ ] Admin token replaced with real auth (or strong rotated token + IP allowlist)
- [ ] Rate limits live on all public endpoints
- [ ] OpenAI keys rotated (the two exposed in dev chat **must** be rotated)

Reliability
- [ ] Graceful fallback on every external API failure
- [ ] Idempotency on inbound webhooks
- [ ] DB migrations run automatically on deploy
- [ ] Nightly backups verified by a test restore

Observability
- [ ] Structured logs shipping to an aggregator
- [ ] Sentry capturing errors
- [ ] Cost + latency visible on dashboard
- [ ] Uptime monitor on `/ready`

Delivery
- [ ] CI green (lint + types + tests) required to merge
- [ ] One-command deploy
- [ ] Rollback path documented

---

## 8. Known risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| OpenAI Realtime cost (~$1.50 / 5-min call) | Budget blow | Per-call $ counter + kill-switch + alerts |
| Meta template rejection | Launch delay | Submit Day 1, expect 1 rejection cycle |
| Twilio↔OpenAI audio format mismatch | Garbled audio | Use `g711_ulaw` on both sides (no resampling) |
| Webhook >15s → Meta retries | Duplicate replies | Fast-ACK + background task (already done in WA) |
| Ephemeral voice transcripts | Data loss | Stream to DB during call (Phase A4) |
| Single EC2 box | SPOF | Phase-2: ALB + 2 instances; for MVP, monitored + backed up |

---

_This document is the source of truth for taking Veerox AI from "WhatsApp working locally" to
"voice + WhatsApp running in production." Build order: **A → (B in parallel) → C → D**._
