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
| `smart_script` | The org's own script answers most turns; the engine decides only callbacks, appointments, WhatsApp sends, hand-offs, opt-outs and closing. For every other scenario the scenario's goal / do / avoid is added to the script prompt (turn log reason `smart_script`, by design, not counted as a fallback). Voice follows the **Voice calls** setting. |
| `human_conversation` | WhatsApp: engine answers first, falls back to script. Voice: follows the separate **Voice calls** setting below. |

### Voice calls setting (per org, used when the org is on Smart Script or Human Conversation)

| Voice mode | Behaviour |
|---|---|
| `off` | The engine does nothing on calls. |
| `shadow` (default) | Log only: records what the engine would do for each caller turn; never changes the call. |
| `live` | The engine decides each turn (intent, scenario, state, next action) and steers the speaking model with a short per-turn instruction; approved actions are run by the backend. |

Set under **Organizations -> Edit -> Voice calls** (API: `PATCH /billing/orgs/{id}` with `conversation_voice_mode`). Details and the real-call test plan: [Voice-Decision-Layer-Test-Checklist.md](Voice-Decision-Layer-Test-Checklist.md).

Set per org by a platform admin: **Organizations -> Edit -> Conversation behaviour**
(API: `PATCH /billing/orgs/{id}` with `conversation_mode`). This is deliberately **not** an
`enabled_features` key: an org with `enabled_features = NULL` means "everything on", which would have
switched the engine on for every unrestricted org.

## What the engine may do

Only these actions, executed by the backend through the existing tools: reply only, `request_callback`,
`transfer_to_human`, `mark_not_interested`, `book_appointment` (needs a stated date and time; asks for them
otherwise) and `send_whatsapp_message` (sends only approved knowledge text; with no matching knowledge it hands
to a human instead). On a live call the engine runs the action itself and the model is told not to call the tool
again, so nothing runs twice. The model proposes at most an action id; anything outside the
allow-list, or missing required arguments, becomes a fallback. Factual answers come only from active
**Approved knowledge** items; with no match the question is handed to a human follow-up, never guessed.

## Scenarios, versions and the Scenario Builder

The engine ships 17 built-in scenarios (greeting, identification, discovery, interested, price question, price objection, discount request, busy, callback request, WhatsApp request, not interested, angry, confused, unknown question, appointment, human handoff, closing). In **Conversation Engine -> Scenarios** an org admin can, without code:

* edit any scenario: situation, example phrases, goal, what the AI should do / must avoid, which approved actions it may use, usual next scenarios, an example reply;
* switch scenarios off, and add custom scenarios (one optional approved action each);
* test in the **Simulator** (plays one customer turn against the draft, the live version or any older version; executes nothing, saves nothing);
* **publish** (new immutable version) and **roll back** (publishes a copy of an older version as a new one).

Rules:

* One draft per org. Published versions never change. The live version is the highest published number; with none published the built-in library is used.
* A conversation keeps the version its first logged turn used. Publishing or rolling back only affects conversations that have not started.
* Publishing is blocked by a regression gate: a fixed set of known phrases ("I am busy", "not interested", "what is the price" ...) must still resolve to the same scenario, and no scenario's example may be understood as a different one. The builder shows the list.
* A scenario can only narrow what the engine does: it can drop an approved action, never add one. Example phrases are matched as phrases inside the message and rank above the generic rules; they also go to the classifier prompt.
* Every draft save, publish, rollback, knowledge change and org mode change writes an audit event (**Versions & audit** tab).

## Blueprint additions

* **When the AI is not sure** (blueprint section 8): a per-version setting in the Scenario Builder: hand the turn to the script (default), ask one short clarifying question, or pass it to a team member. Smart Script always uses the script. It applies on live voice too: a garbled or unclear transcript makes the agent ask the caller to say it again (clarify) or pass the call to a team member (handoff, run once); with the default the call simply carries on as before.
* **Memory** (section 10): `team_size` and `interest_level` (high / medium / low, from what the customer said) are remembered alongside name, business type, pain point, preferred channel and callback time.
* **Internal turn endpoint** (section 22): `POST /internal/conversation/turn` with `conversation_id`, optional `agent_version_id`, `customer_transcript`, `call_context` (`channel`, optional `history`) and `timestamp`. It returns `response_text`, `scenario`, `state`, `intent`, `confidence`, `memory_updates`, `actions` and `fallback_required`. It decides but does **not** execute: approved actions come back as ids with validated arguments for the caller to run through the existing tools. It saves the memory updates and one turn-log row (mode `api`). Tenant comes from the authenticated context; another org's conversation is a 404. Strict Script orgs get `fallback_required: true`.
* **Suggest from my script** (section 13): Scenarios tab -> *Suggest from my script* splits a script into labelled sections (no model needed) and, with the model, proposes approved knowledge and scenarios. Read-only: the script is never edited and nothing is saved until the admin ticks items and adds them. A suggested fact or instruction that contains a number not present in the script is dropped, so a price or duration cannot be invented. Suggested scenarios can only have the action "none".
* **Template library** (section 12): platform admins (`verify_platform_admin`) can share a scenario with every organization (*Share with all organizations* in the editor); any org admin can add one to their own draft from *Template library*. A copy is independent: later edits or deletion of the template never change an org's scenarios. Revision `d0f6b4c8e2a5` adds the `conversation_scenario_templates` table.

## Simulated-call tests (voice behaviour without a phone)

`apps/api/tests/test_voice_simulated_calls.py` drives the real voice adapter with the Realtime events a live call produces, through fake sockets. It checks, among others: a caller talking over the agent cuts the audio (no `response.cancel` is ever sent) and the interruption is answered as the next turn; a cough or an "ok" never triggers an answer; the callee's first "hello?" does not cut the greeting; silence gives a gentle check, a second check, then a goodbye and the `connection_issue` state, a caller's voice resets it, a short pause does nothing and non-live orgs are untouched; garbled transcripts never trigger an action; shadow mode logs what it would do but executes nothing and leaves the spoken reply untouched; a slow decision never delays a reply beyond its budget; and the "when unsure" options. `test_blueprint_conformance.py` maps the blueprint section by section.

These prove the logic. They cannot prove how it sounds, real network latency, or the telephony provider's behaviour: those still need the real-call checklist in [Voice-Decision-Layer-Test-Checklist.md](Voice-Decision-Layer-Test-Checklist.md).

## Metrics and review

**Conversation Engine -> Metrics** (`GET /conversation-engine/metrics`): turns, fallback rate (Smart Script hand-offs excluded), reply time p50 / p95, call completion, appointment conversion, transfer rate, scenarios seen and fallback reasons. **Scenario accuracy** needs a person: mark turns **Right** / **Wrong** in the turn log (optionally the scenario it should have been). Review a sample of shadow-mode voice turns before turning live voice on.

## Silence handling (live voice only)

After the agent finishes speaking, `CONVERSATION_SILENCE_CHECK_SECONDS` (8 s) of caller silence triggers a gentle "are you still there?"; after `CONVERSATION_SILENCE_MAX_CHECKS` (2) such checks and more silence the call is flagged `connection_issue`, the agent says a polite goodbye and the turn log records the state. Any caller speech resets it. Other orgs' calls are untouched.

## Screens and endpoints

* Web: **Conversation Engine** (sidebar, org admins only) — approved knowledge CRUD and the turn log
  (state, intent, scenario, confidence, knowledge used, action, reply, fallback reason, latency).
* API (org admins, `X-Admin-Token`, platform superusers; plain members get 403):
  `GET /conversation-engine/status`, `GET /conversation-engine/turns`, `PATCH /conversation-engine/turns/{id}/review`,
  `/conversation-engine/knowledge` CRUD, `GET /conversation-engine/scenarios/library`,
  `GET /conversation-engine/config`, `GET /conversation-engine/config/versions[/{n}]`,
  `POST /conversation-engine/config/check`, `PUT|DELETE /conversation-engine/config/draft`,
  `POST /conversation-engine/config/draft/publish`, `POST /conversation-engine/config/rollback`,
  `POST /conversation-engine/simulate`, `GET /conversation-engine/audit`, `GET /conversation-engine/metrics`.

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
| `CONVERSATION_VOICE_LIVE` | `true` | Global ceiling for live voice decisions. `false` switches them off everywhere (an org still needs Voice calls = Live). |
| `CONVERSATION_VOICE_DECISION_BUDGET_SECONDS` | `1.0` | Longest a spoken reply waits for a decision; later decisions are dropped and never run an action. |
| `CONVERSATION_VOICE_LLM_TIMEOUT_SECONDS` | `0.9` | Model-call limit inside a voice decision. |
| `CONVERSATION_SILENCE_CHECK_SECONDS` | `8.0` | Caller silence before a gentle check on live voice calls. `0` switches silence handling off. |
| `CONVERSATION_SILENCE_MAX_CHECKS` | `2` | Gentle checks before the call is flagged `connection_issue`. |
| `PLIVO_STARTUP_REGISTRATION` | `true` | Set `false` in every dev `.env`: stops a dev server rewriting the shared Plivo application's answer URL. |

## Deploy

1. Back up / snapshot the database.
2. `alembic upgrade head` (revision `a7c3e1f2b4d6`: `orgs.conversation_mode`, `conversation_turns`,
   `conversation_memory`, `knowledge_items`; revision `b8d4f2a1c6e3`: `orgs.conversation_voice_mode`, default
   `shadow`; revision `c9e5a3b7d1f4`: `conversation_config_versions`, `conversation_audit_events`, and turn columns
   `config_version_id`, `config_version`, `review_*`). Every org stays on `strict_script`; no behaviour change. The deploy workflow runs the migration
   in a one-off container before swapping containers; the changes are additive, so the old containers keep
   working against the migrated database.
3. Deploy API and web.
4. Add approved knowledge for the first org, then switch only that org to Human Conversation.
5. Watch the turn log (filter "Fallbacks only") for the first days.

## Rollback

* **One org:** set its Conversation behaviour back to Strict Script. Effective on the next message.
* **Live voice only:** set the org's Voice calls to Log only or Off (within about 30 seconds), or `CONVERSATION_VOICE_LIVE=false` on the box and recreate the api container.
* **Voice log-only only:** `CONVERSATION_VOICE_SHADOW=false` and restart the API.
* **Everything:** the existing global kill switch still pauses the agent before the engine runs.
* **A bad scenario configuration:** Versions & audit -> **Roll back to this** on the last good version (new conversations use it at once, within about 20 s across workers). Or switch the org to Strict Script.
* **Smart Script:** set the org back to Human Conversation or Strict Script.
* **Silence handling only:** `CONVERSATION_SILENCE_CHECK_SECONDS=0` and recreate the api container.
* **Schema:** `alembic downgrade b8d4f2a1c6e3` drops the builder / audit tables and the turn columns added in `c9e5a3b7d1f4` (scenario versions and audit log are lost); `alembic downgrade 55361bf01ca3` drops everything the engine added.

## Known limits

* Memory holds name, business type, pain point, preferred channel and callback time (callback time call-scoped 30 min, the rest 90 days). Business type and pain point are picked up from sentences like "we run a travel agency" / "our problem is ..."; unusual phrasing is left to the classifier model.
* Rule patterns are a first pass tuned on sample sentences; review fallbacks and add triggers from real chats.
* Voice shadow adds one LLM call per unclear caller turn; watch server load.
* Live voice: rules decide the clear cases in milliseconds, but an ambiguous turn needs a model call that can exceed the budget; then no decision is made and the call continues as today. Measure the model round-trip from the server during the real-call tests.
* Reply language follows the customer's latest message (Hindi script, Hinglish with common spelling variants, English).
* Custom scenarios are recognised from their example phrases (and, when the rules are unsure, the classifier model); write several realistic examples, in each language customers use.
* The regression gate checks rule-based recognition only; model-classified turns are checked in the Simulator and on Shadow Mode review.
* The Simulator and the gate do not replace real-call testing: see [Voice-Decision-Layer-Test-Checklist.md](Voice-Decision-Layer-Test-Checklist.md).
* Not in this phase: e-commerce.
