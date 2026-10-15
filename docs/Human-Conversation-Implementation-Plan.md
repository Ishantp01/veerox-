# Human-Like Conversation Engine — Implementation Plan (Phase 1, no e-commerce)

Source: ADRS quotation dated 9 Oct 2026. **Excluded from this plan:** all e-commerce work (order/stock lookup, store keys, webhooks, mirror, D6/D9), and all Phase 2 items (Scenario Builder UI, versioning/publish/rollback, Simulator, live voice steering, barge-in/silence handling).

Duration: 9 working days after DFD sign-off (original 10 minus the e-commerce day).

## 1. Scope

| In | Out |
|---|---|
| Intent / scenario / emotion detection + confidence (rules first, one LLM call for ambiguous turns) | E-commerce lookup tools, connectors, store credentials |
| Conversation state + structured memory with expiry | Webhooks, data mirror, abandoned-cart / COD |
| Answers only from approved knowledge; unknown -> follow-up or human handoff | Scenario Builder admin screens, versioning, Simulator |
| Model requests only approved action ids; backend executes via existing tools | Live voice steering of the realtime call |
| Fallback to existing script path on timeout / invalid output / low confidence | |
| Per-org mode flag (Strict Script / Human Conversation) + existing kill switch | |
| Per-turn log, viewable by authorised users | |
| WhatsApp live (behind flag); voice shadow (log-only) | |

## 2. Existing code we build on

- `apps/api/core/agent.py` — `AgentCore.handle_turn` is the one entry point for WhatsApp (`channels/whatsapp/adapter.py:464`) and the CLI. Kill switch via `_is_kill_switch_active()` (Redis key `veerox:kill_switch`).
- `apps/api/core/tools.py` — `TOOL_DEFINITIONS` / `DISPATCH_TABLE` are the approved action set (`capture_lead`, `book_appointment`, `request_callback`, `transfer_to_human`, `qualify_lead`, `mark_not_interested`, `send_whatsapp_*`, `initiate_ai_call`, `lookup_customer`).
- `apps/api/channels/voice/adapter.py` + `realtime_bridge.py` — `create_response=False`; the code fires each `response.create` after a turn commits. Shadow hook goes just before that.
- `db/models/org.py` — `AVAILABLE_ORG_FEATURES`, `Org.enabled_features`; `deps.py::is_org_feature_enabled`.
- Latest alembic head must be confirmed with `alembic heads` before writing the migration (`migrations/versions/`, 60+ files).

## 3. Design decisions (and one trap)

1. **Flag trap.** `Org.enabled_features = NULL` means *unrestricted* (every feature on). If `human_conversation` is just another key in that list, every NULL org would get the new engine. So the new mode must be **opt-in**: store it as its own column `orgs.conversation_mode` (`strict_script` default | `human_conversation`), editable via the existing admin org update (`OrgUpdateIn`). Do not use `enabled_features` for this.
2. **Engine is a wrapper, not a rewrite.** In `handle_turn`, after the kill-switch check: if mode is `human_conversation` and channel is `whatsapp`, call the engine; on any failure return the existing script-path result. Strict Script path stays byte-for-byte unchanged.
3. **LLM never executes anything.** It returns JSON `{intent, scenario, emotion, confidence, action_id, args}`. The backend validates `action_id` against an allow-list and runs it through `_dispatch_tool`.
4. **24-hour WhatsApp window.** Sending still goes through existing tools (they already handle the outside-window template fallback from the recent commits). The engine decides; it does not send directly.
5. **Voice = shadow only.** Engine runs with a hard timeout (target <= 800 ms) in a fire-and-forget task, writes the turn log, never alters `response.create`.

## 4. Data model (one migration)

New package `apps/api/core/conversation/` and models in `db/models/`:

- `conversation_turns` (D2): id, org_id, conversation_id, channel, mode (`live`|`shadow`), user_text, state, intent, scenario, emotion, confidence, knowledge_ids (JSON), action_id, action_result, reply_text, fallback_reason (nullable), latency_ms, created_at. Index (org_id, conversation_id, created_at).
- `conversation_memory` (D3): id, org_id, user_id, key, value, scope (`call`|`persistent`), expires_at, updated_at. Unique (org_id, user_id, key, scope). Keys limited to: name, business_type, preferred_channel, callback_time.
- `knowledge_items` (D4): id, org_id, kind (`product`|`pricing`|`policy`|`faq`), title, body, language, active, updated_at.
- `orgs.conversation_mode` column.

Every table org-scoped; every query filters by `org_id`.

## 5. Modules

| Module | Responsibility |
|---|---|
| `understand.py` | Keyword/regex rules for clear cases (busy/callback, price, not interested, human handoff, angry) in Hindi/English/Hinglish; one `chat_completion` JSON call when rules are ambiguous. Returns intent, scenario, emotion, confidence. |
| `memory.py` | Read/write structured facts; expiry rules (call-scoped ends with conversation; persistent 90 days, refreshed on use). |
| `knowledge.py` | Retrieve approved items for the org/language; if none match, signal "unknown". |
| `decide.py` | Policy: map scenario + memory + knowledge to one allowed action id. Allow-list: `request_callback`, `book_appointment`, `capture_lead`, `qualify_lead`, `mark_not_interested`, `transfer_to_human`, `send_whatsapp_message`, `none`. Unknown question -> follow-up (`request_callback`) or `transfer_to_human`. |
| `reply.py` | Short voice-friendly phrasing of the permitted answer only; language matched to the user. |
| `engine.py` | Orchestrates 1.0-6.0, timeouts, fallback reasons, writes the turn row. |
| `shadow.py` | Voice hook: same pipeline, log-only. |

Fallback triggers: LLM timeout (default 6 s live), invalid/unparseable JSON, action id not in allow-list, confidence below threshold (default 0.6). Each logs `fallback_reason`.

## 6. Day-by-day

| Day | Work | Done when |
|---|---|---|
| 1-2 | DFD sign-off (Levels 0, 1B; 7.0 reduced to "mode flag"), answers to the 3 remaining confirmation points (languages, first org, approver), confirm alembic head, agree thresholds | Written sign-off received |
| 3 | Migration + models (D2, D3, D4, `conversation_mode`); `memory.py` with expiry; model tests | `alembic upgrade head` clean on fresh DB and on a copy of prod schema |
| 4 | `understand.py`: rules + single LLM decision call, 9 scenarios x 3 languages; unit tests with a table of sample utterances | >= 90% on the sample set for rule-covered scenarios |
| 5 | `knowledge.py`, `decide.py`, action allow-list and arg validation against `DISPATCH_TABLE` | Invalid action ids and bad args rejected in tests |
| 6 | `reply.py`, `engine.py`, fallback paths, turn-log writes | Each fallback trigger has a passing test |
| 7 | Wire into `handle_turn` (WhatsApp only, behind `conversation_mode`); admin endpoint to set mode; knowledge CRUD endpoints (admin, org-scoped) | Strict orgs unchanged; flagged org uses engine |
| 8 | Turn-log read endpoint + minimal admin view (authorised users only); voice shadow hook with timeout | Shadow never delays `response.create` (measured) |
| 9 | Scenario regression tests, staged rollout on one org, handover doc, rollback runbook | Mode flip to `strict_script` or kill switch restores old behaviour immediately |

## 7. Testing

- Unit: classifier table tests, memory expiry, allow-list enforcement, each fallback trigger, org scoping (org A cannot read org B's turns/knowledge/memory).
- Integration: `handle_turn` with mode off vs on; WhatsApp adapter path with a fake LLM; outside-24h-window behaviour unchanged.
- Voice: shadow task failure/timeouts do not affect the call; latency measured with the existing `/diag/latency` probe.
- Existing `apps/api/tests/test_agent_core.py` must pass unchanged.

## 8. Rollout and rollback

1. Deploy with every org on `strict_script` (no behaviour change).
2. Enable `human_conversation` for one org on WhatsApp only.
3. Review the turn log with the client; tune thresholds/knowledge.
4. Voice shadow on for the same org; review logs before any future live-voice phase.
5. Rollback: set org back to `strict_script` (instant), or kill switch (global).

## 9. Risks / open points

- Extra LLM call per ambiguous turn adds cost and latency; rules-first keeps most turns at zero extra calls.
- Voice shadow adds load on the t3.medium; keep it behind its own env switch so it can be turned off without a deploy.
- Knowledge quality is the client's input; the engine will only be as good as the approved items supplied.
- Revised price for the reduced scope (₹30,000 included e-commerce) is a commercial decision for ADRS/client — not decided here.
