# Phase 0 — Audit and insertion points

Quotation Phase 0 deliverable: where the Conversation Intelligence Engine plugs into the existing platform, and what it reuses. No existing path was replaced; every insertion is behind a per-org setting with the previous behaviour as fallback.

## Call and message lifecycle

| Step | Code | Engine involvement |
|---|---|---|
| WhatsApp inbound | Meta webhook -> `AgentCore.handle_turn` (`core/agent.py`) | **Insertion point 1.** Before the script / tool-calling loop, `run_engine` runs when the org is on Smart Script or Human Conversation. No reply (or any fallback) -> the unchanged script path runs. |
| Voice call setup | Plivo / Twilio webhook -> `realtime_bridge.py` -> OpenAI Realtime session | None. Provider, STT, TTS and the speaking model are unchanged. |
| Caller transcript | `conversation.item.input_audio_transcription.completed` in `channels/voice/adapter.py` | **Insertion point 2 (shadow).** `schedule_voice_shadow` logs what the engine would do; never changes the call. |
| Voice reply | `_fire_pending_reply` -> `response.create` | **Insertion point 3 (live).** `compute_voice_plan` runs in the background during the existing answer gap; if ready within the budget its instruction is added to that one `response.create`, otherwise the payload is exactly as before. |
| Agent finished speaking | `response.done` handler | **Insertion point 4.** `_arm_silence` starts the silence clock (live voice orgs only). |
| Actions | `core/tools.py` `DISPATCH_TABLE` (`request_callback`, `book_appointment`, `send_whatsapp_message`, `transfer_to_human`, `mark_not_interested`) | Reused unchanged. The engine only proposes an id; `validate_action` checks the allow-list and required arguments; the backend runs it. |

## Reused as-is

Org model and `enabled_features` / license status / global kill switch; member data scoping (the engine screens are org-admin only); encrypted per-org credentials and OpenAI key resolution; usage metering; leads, appointments, follow-up and callback workers; the script engine (fallback and Smart Script base).

## New

Tables: `conversation_turns`, `conversation_memory`, `knowledge_items`, `conversation_config_versions`, `conversation_audit_events`. Org columns: `conversation_mode`, `conversation_voice_mode`. Code: `core/conversation/` (understand, decide, reply, engine, memory, knowledge, config, builder, simulate, metrics, silence, voice_decision, shadow) and two routers.

## Risks found and how they are contained

| Risk | Containment |
|---|---|
| Latency on voice | Rules decide clear cases in ~0 ms; one model call with a hard timeout (0.9 s); decision budget 1 s; late decisions are dropped and never run an action. |
| Large voice adapter | Per-org switch (off / shadow / live) plus global `CONVERSATION_VOICE_LIVE`; any error or timeout returns today's payload. |
| Double actions on a call | Engine runs the action and sets `tool_choice: none` for that response; idempotency key per conversation + action. |
| Mid-call configuration change | A conversation is pinned to the config version of its first logged turn. |
| Dev shares production numbers | Test org with its own number is a prerequisite for real-call tests (see the checklist). |
| Model inventing facts | Factual answers only from active approved knowledge; no match -> human follow-up. |

## Open items (not code)

Real-call verification, client DFD sign-off and Section 6 answers, approved knowledge content, and e-commerce (excluded from this scope).
