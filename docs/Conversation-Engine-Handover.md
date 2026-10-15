# Conversation Intelligence Engine — Handover and Rollback

Phase 1, without e-commerce. Plan: [Human-Conversation-Implementation-Plan.md](Human-Conversation-Implementation-Plan.md).

## What it is

An optional layer in `AgentCore.handle_turn` (`apps/api/core/agent.py`). When an org is in
**Human Conversation** mode, every inbound WhatsApp message first goes through the engine
(`apps/api/core/conversation/`). If the engine cannot answer safely it returns nothing and the
existing script / tool-calling path runs exactly as before.

Per turn: understand (rules, then one LLM call only when unclear) -> update memory -> decide one approved
action -> phrase a short reply -> run the action through the existing tool -> log the turn.

## Modes

| Mode | Behaviour |
|---|---|
| `strict_script` (default for every org) | Unchanged. Engine never runs. |
| `human_conversation` | WhatsApp: engine answers first, falls back to script. Voice: log-only shadow, never changes the call. |

Set per org by a platform admin: **Organizations -> Edit -> Conversation behaviour**
(API: `PATCH /billing/orgs/{id}` with `conversation_mode`). This is deliberately **not** an
`enabled_features` key: an org with `enabled_features = NULL` means "everything on", which would have
switched the engine on for every unrestricted org.

## What the engine may do

Only these actions, executed by the backend through the existing tools: reply only, `request_callback`,
`transfer_to_human`, `mark_not_interested`. The model proposes at most an action id; anything outside the
allow-list, or missing required arguments, becomes a fallback. Factual answers come only from active
**Approved knowledge** items; with no match the question is handed to a human follow-up, never guessed.

## Screens and endpoints

* Web: **Conversation Engine** (sidebar, org admins only) — approved knowledge CRUD and the turn log
  (state, intent, scenario, confidence, knowledge used, action, reply, fallback reason, latency).
* API (org admins, `X-Admin-Token`, platform superusers; plain members get 403):
  `GET /conversation-engine/status`, `GET /conversation-engine/turns`, `/conversation-engine/knowledge` CRUD.

## Fallback reasons (turn log)

| Reason | Meaning |
|---|---|
| `timeout` | Classification LLM call exceeded `CONVERSATION_LLM_TIMEOUT_SECONDS` (6 s). |
| `invalid_output` | LLM returned unusable JSON / unknown scenario. |
| `low_confidence` | Below `CONVERSATION_MIN_CONFIDENCE` (0.6). |
| `invalid_action` | Proposed action not on the allow-list or missing arguments. |
| `action_failed` | The tool returned an error. |
| `llm_error` | Provider error. |
| `error` | Unexpected internal error (logged with a stack trace). |

## Settings (environment)

| Variable | Default | Purpose |
|---|---|---|
| `CONVERSATION_VOICE_SHADOW` | `true` | `false` stops voice shadow entirely (no extra LLM calls). |
| `CONVERSATION_LLM_TIMEOUT_SECONDS` | `6.0` | Per classification/phrasing call. |
| `CONVERSATION_MIN_CONFIDENCE` | `0.6` | Below this the script path answers. |

## Deploy

1. Back up / snapshot the database.
2. `alembic upgrade head` (revision `a7c3e1f2b4d6`: `orgs.conversation_mode`, `conversation_turns`,
   `conversation_memory`, `knowledge_items`). Every org stays on `strict_script`; no behaviour change.
3. Deploy API and web.
4. Add approved knowledge for the first org, then switch only that org to Human Conversation.
5. Watch the turn log (filter "Fallbacks only") for the first days.

## Rollback

* **One org:** set its Conversation behaviour back to Strict Script. Effective on the next message.
* **Voice shadow only:** `CONVERSATION_VOICE_SHADOW=false` and restart the API.
* **Everything:** the existing global kill switch still pauses the agent before the engine runs.
* **Schema:** `alembic downgrade 55361bf01ca3` drops the new tables and column (turn log and knowledge are lost).

## Known limits

* Memory holds only name, business type, preferred channel, callback time (call-scoped 30 min, others 90 days).
* Rule patterns are a first pass tuned on sample sentences; review fallbacks and add triggers from real chats.
* Voice shadow adds one LLM call per unclear caller turn; watch server load.
* Not in this phase: scenario builder UI, versioning, simulator, live voice steering, e-commerce.
