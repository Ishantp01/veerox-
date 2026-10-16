# Remaining Scope — Implementation Plan (quotation items only, no e-commerce)

Covers only what the two quotations promise:

* **Phase 1 quotation** (Human-Like AI Calling & Conversation Intelligence Engine, 9 Oct 2026) — close-out of the items not yet delivered.
* **Decision Layer quotation** (AI Voice Calling Agent – Decision Layer) — drive the live voice call with the same intent / emotion / scenario / state / next-action logic.

Explicitly **out of scope** unless the client adds it: test-numbers allow-list, self-serve mode toggle, Smart Script mode, Scenario Builder screens, simulator, agent versions with publish/rollback, extra scenarios/states, audit log, rollout metrics, e-commerce.

> **Update 2026-10-16.** The signed 9 Oct quotation (PDF) does include the items listed above as out of scope: Smart Script, Scenario Builder, simulator, agent versions with publish / rollback, the full scenario library, the audit log, silence handling, `pain_point` memory and rollout metrics. They are now built (see [Conversation-Engine-Handover.md](Conversation-Engine-Handover.md)): migration `c9e5a3b7d1f4`, `core/conversation/{config,builder,simulate,metrics,silence}.py`, `routers/conversation_engine_config.py`, and the Scenarios / Simulator / Versions & audit / Metrics tabs. Still open: real-call verification (A3, B6), client DFD sign-off and Section 6 answers, and e-commerce (excluded).

## 0. Status today (deployed in production, commit b6f9fea)

Done: intent / scenario / emotion + confidence, state, structured memory with expiry, approved knowledge with handoff on unknown, fallback to the script path, 8 priority scenarios, per-turn log + admin page, per-org mode with instant rollback, WhatsApp live, voice log-only, handover doc. Verified on production with four real WhatsApp messages.

## 1. Work package A — Phase 1 close-out (about 3 days)

### A1. Wire the two missing approved actions (1 day)

Quotation 2.1: "the backend runs them through the existing tools (callback, appointment, WhatsApp send, transfer to human)". Today the allow-list is `none, request_callback, transfer_to_human, mark_not_interested`.

* Add `book_appointment` and `send_whatsapp_message` to `ALLOWED_ACTIONS` ([types.py](../apps/api/core/conversation/types.py)); `validate_action` already checks required arguments against `TOOL_DEFINITIONS` (`date`+`time`; `message`).
* Triggers (**decision needed, see section 5**: the quotation names the tools but not the scenarios):
  * customer states an explicit date **and** time for a demo/appointment -> `book_appointment` (reuse the callback date/time extraction in `understand.py`);
  * customer asks for details to be sent on WhatsApp (mainly voice) -> `send_whatsapp_message` with the matched approved knowledge text.
* Everything else about the pipeline is unchanged: the model may only propose an id, `decide.py` validates, the backend runs it through `_dispatch_tool`.
* Tests: allow-list accepts both ids and rejects missing args; each trigger produces exactly one tool call; failure -> fallback (`action_failed`).

### A2. Hinglish language detection (0.5 day)

Quotation 2.2: replies in Hindi, English and Hinglish. `detect_language` uses a small marker list, so "kal shaam 5 bje call krna" is classed English.

* Extend the marker list with common spelling variants (shaam, subah, dopahar, bje, baje, krna, karna, kijiye, nahi/nhi, ...) and normalise repeated letters.
* Add a test table of at least 40 utterances (Hindi script, Hinglish with variant spellings, English); target >= 95 % correct.
* No model change; the reply language still follows the customer's current message.

### A3. Voice log-only check on a real call (0.5 day plus a real call)

Quotation 2.3: "voice runs in log-only (shadow) mode". Built and unit-tested, never run on a phone call.

* Needs a **test org with its own number** (dev currently shares production's Plivo/Twilio/WhatsApp numbers; see section 5).
* Place 5 scripted calls; confirm one `shadow` row per caller turn appears in the turn log, nothing is executed, call quality unchanged, and the extra model call does not slow the call.

### A4. Documentation and handover refresh (0.5 day)

Update [Conversation-Engine-Handover.md](Conversation-Engine-Handover.md): new actions, language handling, voice shadow results. List the client-side items (DFD sign-off, answers to the Section 6 points, approved knowledge content).

### A5. Acceptance (A)

* Allow-list covers all four tools named in the quotation; no tool runs outside it.
* Language test set >= 95 %.
* Voice shadow rows present for all caller turns of 5 test calls, zero actions executed.
* Full API suite green.

## 2. Work package B — Decision Layer on live voice (8–10 days, per the quotation)

Goal: the voice agent decides its next move from intent, emotion, scenario and state (and the existing memory), instead of only following the script. Additive layer, with the existing behaviour as fallback.

### B0. Design and audit (1 day)

The voice path is in [adapter.py](../apps/api/channels/voice/adapter.py): the caller's transcript arrives in `conversation.item.input_audio_transcription.completed`; the reply is released by `_fire_pending_reply` / `_fire_reply_when_quiet` as a plain `response.create`. The adapter already sends per-response `instructions` (`_oob_response`), so a decision can be injected the same way. Write a one-page design covering the hook point, who executes actions, the latency budget and the flag.

### B1. Voice decision entry point (2 days)

New `core/conversation/voice_decision.py` reusing `understand`, `decide`, `memory` and `knowledge`:

* Input: the caller's transcript, call state, org id, user id, conversation id.
* Output: scenario, emotion, state, confidence, a short **instruction** for the speaking model (what to do, what to avoid, approved facts to use) and an optional approved action.
* No phrasing model call: the realtime model speaks. At most one classification call, with a hard timeout (~700 ms); on timeout or low confidence return nothing, so today's behaviour continues.
* Rules cover the clear cases (busy, callback, not interested, angry, human) at ~0 ms.

### B2. Hook into the call (2 days)

* In `_fire_pending_reply`, start the decision in the background as soon as the transcript is final, so it runs during the existing ~1 s answer gap and hides most of its latency.
* If a decision is ready in time, send `response.create` with `instructions = base instructions + decision`; otherwise send the plain `response.create` exactly as today.
* Cover both TTS paths (OpenAI voice and ElevenLabs) and keep the interruption, filler and acknowledgement logic untouched.

### B3. Actions on a call (1 day)

Prevent double actions (the voice model can already call tools on its own):

* The engine executes the approved action itself through `_dispatch_realtime_tool`'s underlying handler, then tells the model the result in the instruction ("callback booked for 5 pm; confirm briefly").
* For that response set `tool_choice: "none"` so the model cannot run the tool a second time.
* Idempotency guard keyed on conversation + action id.

### B4. Memory continuity (1 day)

* Persist facts learned on calls (name, callback time) to `conversation_memory`; load them at call start so a returning customer is not asked again.
* The existing conversation history keeps working unchanged.

### B5. Safety switches (0.5 day)

* Voice **live** must be its own switch, not tied to WhatsApp: add a per-org voice setting (off / shadow / live; default shadow for orgs already on Human Conversation) plus env kill switch `CONVERSATION_VOICE_LIVE=false`.
* Any error, timeout or low confidence -> existing behaviour.

### B6. Testing and rollout (2–3 days)

* Unit tests: decision builder, hook with a fake realtime socket (asserts the exact `response.create` payload), timeout and flag behaviour, no double execution.
* Scripted real-call checklist (busy, callback, price, objection, interested, angry, human, not interested, unknown, confusion), each pass/fail.
* Rollout: shadow for 2–3 days -> compare the log with what actually happened -> live on the test org -> one real org. Rollback = flip the voice setting.

### B7. Acceptance (B)

* On the checklist, the decision matches the expected scenario and action for >= 90 % of scripted utterances.
* Added reply latency: p95 <= 300 ms when rules decide, <= 900 ms when the model decides; never blocks the call (timeout falls back).
* Zero duplicate actions across the test calls.
* Any failure falls back to today's behaviour without the caller noticing.
* Full API suite green.

## 3. Timeline

| Days | Work |
|---|---|
| 1–3 | A1–A4 (close-out) |
| 4 | B0 design |
| 5–6 | B1 voice decision entry point |
| 7–8 | B2 hook into the call |
| 9 | B3 actions on a call, B5 switches |
| 10 | B4 memory continuity |
| 11–13 | B6 testing, shadow period, live on test org |

The Decision Layer quotation says 8–10 working days; this fits if A is done first and real test calls are available from day 5.

## 4. Risks

* **Latency** on voice: mitigated by the background decision, the 700 ms timeout and the fallback.
* **Regression** in the 1,300-line voice adapter: mitigated by the per-org switch, shadow first and a fake-socket test of the exact payload.
* **Double actions** on a call: mitigated by `tool_choice: none` and the idempotency guard.
* **No safe test line**: dev shares production numbers (fixed for Plivo by `PLIVO_STARTUP_REGISTRATION=false`, but not for calls/WhatsApp); a test number is a prerequisite for A3 and B6.

## 5. Decisions and inputs needed

1. **Triggers for `book_appointment` and `send_whatsapp_message`** (A1): confirm the two rules above, or give the client's wording.
2. **Voice mode semantics** (B5): confirm a separate per-org voice switch with default "shadow".
3. **Test number** for voice tests (a separate Plivo or Twilio number, or a Meta test number): who provides it.
4. **Price** for the Decision Layer quotation (blank in the document), and confirmation that it is not charged twice with Phase 1.
5. **Client paperwork** for Phase 1: DFD sign-off and the Section 6 answers.
6. **Note to the client:** the per-org mode is a dedicated `conversation_mode` column, not an entry in the existing feature-flag list (an empty list means "all features on", which would have switched the engine on for every org). Behaviour matches the quotation: per-org mode, instant rollback, existing kill switch.

---

# Roadmap across all blueprint phases (0–7)

Phases follow the architecture blueprint (section 27). Each phase is marked:
**[P1]** covered by the Phase 1 quotation, **[DL]** covered by the Decision Layer quotation, **[NEW]** in neither quotation (needs a new quotation or change order). E-commerce is excluded throughout.

| Phase | Blueprint goal | Status today | What is left | Covered by |
|---|---|---|---|---|
| 0 Audit | Understand the existing repo and call flow before changing it | Done informally (codebase analysis table in the Phase 1 quotation; voice and WhatsApp paths mapped) | Write the audit up as a short document (call lifecycle, hook points, risks) | [P1] |
| 1 Data contracts | Schemas for config, scenarios, state, memory, actions, decisions, audit | Partly: turns, memory, knowledge, org mode exist | Agent versions, scenarios, scenario versions, transitions, publications, audit events | [NEW] |
| 2 Engine in isolation | Decision engine with mocked transcripts | Done (88 tests); 8 priority scenarios | Package A1 and A2 (two missing actions, Hinglish); optional extra scenarios (customer identification, requirement discovery, discount, WhatsApp info, appointment, closing) | A1, A2 [P1]; extra scenarios [NEW] |
| 3 Scenario Builder | Admin UI + API to create, edit, test, version, publish scenarios | Not built (quotation lists it as a later phase) | Scenarios as data, CRUD API and UI, examples per scenario, simulator, draft/publish/rollback, audit log, permissions | [NEW] |
| 4 Shadow mode | Run on live transcripts without speaking; compare with real outcomes | Voice shadow built; not verified on a real call | A3 (real-call check); a way to compare logged decisions with call outcomes | A3 [P1]; comparison tooling [NEW] |
| 5 Controlled live | Enable per agent/tenant with flags; keep the fallback | WhatsApp live per org with instant rollback | Voice live (package B) | B [DL] |
| 6 Optimization | Latency, cost, coverage, voice behaviour, retrieval, conversion | Not started | Tune rules and thresholds from production turn logs; latency and fallback-rate reporting; knowledge retrieval tuning | [NEW] (partly B7 acceptance) |
| 7 Full production | Human Conversation as mature default, legacy fallback kept | Not started | Make the default per client decision after reviewing results | Client decision |

## Order of work

1. **Now: package A** (Phase 2 close-out plus Phase 4 verification), 3 days. All inside the Phase 1 quotation.
2. **Package B** (Phase 5, voice live), 8–10 days. Inside the Decision Layer quotation. Needs a test number and the price filled in.
3. **Phase 3 Scenario Builder with Phase 1 data contracts** (versions, scenarios as data, simulator, publish/rollback, audit), est. 10–12 working days. Needs its own quotation; both existing quotations leave it out, but the blueprint's success criteria ("an administrator can configure an agent without editing source code") depend on it.
4. **Phase 6 optimization**, ongoing after real traffic exists; reports first, then rule and knowledge tuning.
5. **Phase 7** is a rollout decision, not development: one org at a time, review the turn log, keep the per-org switch.

## Dependencies between phases

* Scenario Builder (3) needs the data contracts (1) first, and should reuse the engine unchanged: scenarios move from code constants (`_RULES`, `decide.py`) into tables read at runtime, with the code versions kept as defaults.
* Versioning (3) must snapshot configuration at call start; the voice hook (B2) should therefore read the configuration once per call.
* Voice live (5) should land before the Builder, otherwise scenarios are only editable for WhatsApp.
* Optimization (6) needs the turn log and, for voice, the shadow period of B6.

## Estimates

| Block | Days | Quotation |
|---|---|---|
| Phase 0 write-up | 0.5 | P1 |
| Package A (Phase 2 close-out, Phase 4 check) | 3 | P1 |
| Package B (voice live) | 8–10 | DL |
| Phase 1 data contracts + Phase 3 Scenario Builder, simulator, versioning | 10–12 | NEW |
| Extra scenarios, states and memory fields | 3–4 | NEW |
| Phase 6 reporting and tuning | 3–5, then ongoing | NEW |
