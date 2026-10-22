# Phase 8D further validation — PASS/FAIL report

21 October 2026. **Overall: FAIL / HOLD for paid calling.** All requested checks were attempted; unresolved failures and unavailable running-process evidence are listed explicitly. This is a validation report, not authorization to dial.

The user's later instruction waived credential rotation. No credentials were rotated, and rotation is not certified. No live calls, paid provider requests, production writes, configuration activation, deployments, commits, or pushes were performed. Local browser-test servers were stopped.

## Results

| Requested check | Result | Evidence / qualification |
|---|---|---|
| Complete API suite | **FAIL** | 1,935 passed, two assertion failures, three temporary-directory setup errors; 1,940 collected cases; six warnings; 681.77 seconds. Three setup cases subsequently passed with the corrected workspace-local harness. Two old call-control assertions remain failing. |
| Web unit suite | **FAIL** | 19 passed, six failed across four test files. All six failures are in `nav.test.tsx`: `Nav` requires `AuthProvider`, which the tests do not provide. |
| Browser suite | **FAIL / incomplete** | Login smoke test passed; authenticated flow skipped because no isolated backend was supplied. Next's font fetches were blocked by the sandbox. Teardown did not finish, so the test process was stopped; no clean suite exit is claimed. |
| Database / Redis credentials | **PASS for current authentication; rotation waived** | Read-only PostgreSQL connection succeeded; declared pilot DB matched; `transaction_read_only=on` verified. Redis `PING` succeeded. These are authentication checks, not rotation proof. |
| Intended-org configuration audit | **PASS for audit; FAIL for business readiness** | Selected strictly by pilot `DEFAULT_ORG_ID`, with org-scoped queries. No knowledge items, scripts, custom greeting, legacy script, or published config. |
| Feature-value verification | **PASS for loaded pilot settings; FAIL for serving-process attestation** | Four requested flags resolve false. No server responded on `127.0.0.1:8002/diag/voice-config`; no in-memory server snapshot was available. |
| Offline voice simulations | **PASS** | 201 tests passed for disconnections, reconnect context/isolation, interruptions, TTS races, tool overlap, action deduplication, playback, and simulated calls. Two additional call-control lifecycle simulations passed. |
| Expanded logging verification | **FAIL** | Five of eleven adversarial cases passed; six leaked a tested class of synthetic sensitive data. No real secrets or customer speech were used as test payloads. |
| Controlled-call checklist | **PASS as a prepared document; FAIL as an executable release gate** | Expected-cost assumptions, duration, success criteria, rollback and proposed ₹100 ceiling documented. Hard spending enforcement is not present/verified. Current approved spend remains zero. |

These test counts overlap. Do not add the simulation or rerun counts to the full-suite total as distinct tests.

## Full-suite findings

The complete Python suite was run against synthetic credentials and in-memory SQLite. External socket connections were blocked in the test process; local HTTP requests used test transports. No application lifespan was started against pilot or production data. The earlier deterministic playback-clock adjustment remains in place.

The first run's temporary path was `.validation-8d/tmp-full`; its parent did not exist when three `tmp_path` fixtures first ran. The reusable runner now creates artifact parent directories before pytest starts. The environment-selection test also assumed no `VEEROX_ENV_FILE` override; it now explicitly clears that override while testing path resolution, without loading the actual `.env`. All three affected fixtures pass on recheck.

The two remaining API failures are:

- `test_end_call_with_no_goodbye_yet_asks_for_one_before_hanging_up`
- `test_end_call_when_disabled_is_refused_and_the_call_carries_on`

Both assert that `response.create` is sent immediately from `response.function_call_arguments.done`. Phase 8D instead queues continuation until the function-calling response finishes, avoiding the active-response conflict. They remain visible as failures; they were not changed merely to make this report green. The separate realistic lifecycle tests verify that no response overlaps, goodbye is requested after the tool response finishes, the actual goodbye completes before hangup, and a disabled end-call request resumes exactly once after completion. Both passed. Updating the older expectations and obtaining a clean complete-suite run remain review items.

A focused recheck of call control and the three setup cases initially produced 48 passes and three failures: the same two old call-control assertions plus the shell-override assumption. After the latter test's prerequisite was corrected, the three setup cases passed. No final all-green complete-suite run is claimed.

The six API warnings comprise Alembic's missing `path_separator` deprecation and duplicate FastAPI operation IDs for the health/ready and voice webhook routes. No production migrations or router edits were made.

## Read-only organization audit

The audit used the pilot env file with no shell override of its entries. Known-production-host checks passed. PostgreSQL enforced the read-only transaction before the application queries; the transaction was rolled back. Redis was limited to `PING`. No user transcript, message body, customer number, credential value, or provider response payload was exported.

| Setting / resource | Current audited value |
|---|---|
| Conversation mode | `strict_script` |
| Voice decision mode | `shadow` |
| Global `CONVERSATION_VOICE_LIVE` | `true`; live decisions still ineffective for this org |
| Custom greeting / brand | Both absent |
| Effective brand | `WorkAssign AI`, from the platform fallback; owner approval not established |
| Greeting behavior | Platform instruction for a short Hindi greeting with AI disclosure; not fixed spoken wording |
| Business knowledge | 0 total, 0 active |
| Scripts | 0 rows; no legacy org script |
| Published configuration | None; built-in defaults apply |
| `enhanced_dialogue` | `false`, from built-in defaults |
| `VOICE_RECONNECT_RECOVERY` | `false` |
| `VOICE_SMART_TURN_TAKING` | `false` |
| `VOICE_TTS_RECOVERY` | `false` |
| `VOICE_SAFE_CANCEL` / nonblocking TTS / prewarm | All `false` |
| Natural Hinglish / speech normalization | Both `false`; no org voice profile |
| Recording mode | `off` |
| `LOG_TRANSCRIPTS` / `LOG_PRIVACY_ENABLED` | `false` / `true` |
| Workers / provider config changes / startup registration | All `false` |
| Active cost-rate rows | 0 |

The fallback greeting template is: “Namaste, main WorkAssign AI ka AI assistant Veer bol raha hoon. Main aapki kya madad kar sakta hoon?” It is a prompt example, not a claim about exactly what a future model will say. It must not be treated as an approved business identity.

The enabled built-in scenario definitions list `none`, `request_callback`, `transfer_to_human`, `mark_not_interested`, `book_appointment`, and `send_whatsapp_message` as their action union. They are not evidence that live voice decisions are active.

The legacy Realtime tool catalog exposes `book_appointment`, `capture_lead`, `initiate_ai_call`, `lookup_customer`, `mark_not_interested`, `qualify_lead`, `request_callback`, `send_whatsapp_file`, `send_whatsapp_message`, `send_whatsapp_template`, and `transfer_to_human`. `enabled_features` is null, which imposes no module restriction. Tool exposure is distinct from owner approval and tool-specific runtime checks. No business-specific action approval was found in a published version. Paid side-effect tools, particularly initiating another call or sending messages, must be constrained before the proposed experiment.

Other preflight warnings remain: a neutral disposable database name, the pilot tunnel matching the main env, shared non-database/Redis provider credentials, and unneeded billing/monitoring-related values. No values are disclosed here and none were changed. Distinct DB/Redis targets do not by themselves isolate provider accounts or shared routing.

## Logging: reproduced gaps

`scripts/pilot_logging_verify.py` uses the real configured logging formatter and synthetic markers, captures all generated log output in memory, and saves only per-case PASS/FAIL. With privacy on and transcripts off:

- **PASS:** structured caller number; Uvicorn caller URL including encoded `+`; caller transcript suppression; assistant transcript suppression; phone in a formatted exception trace.
- **FAIL:** database connection URL credentials; Redis URL credentials; nested authorization headers; nested webhook parameters containing caller number/transcript; nested provider-error payload; raw provider response body.

The existing processor handles selected fields and shallow structures; arbitrary credentials and deeper payload fields can bypass it. Real voice callbacks log `params` on missing-field paths, and Twilio call errors can log a response `body`, so these are relevant paths, not only imaginary field names. The passing Phase 8D tests did not cover these classes of leakage. This verification therefore supersedes any interpretation that the prior logging fixes guaranteed comprehensive redaction.

No real secrets were injected into logging to perform these checks. Historical logs were not rewritten or deleted. Fix the redaction/projection policy and add regressions before approving a call; this validation did not change runtime logging behavior.

## Spending and controlled-call gate

See [the controlled-call checklist](pilot-controlled-call-checklist.md) for the priced assumptions, sources, test sequence, acceptance thresholds and rollback. It proposes 60–90 connected seconds, a 120-second provider-side maximum, an approximately ₹40 planning allowance, and a **₹100 hard ceiling**, subject to separate approval and verified account tariffs. These are future limits, not authorized or already enforced values.

No application/provider duration bound or verified aggregate budget reservation was found on the current outbound-call path. Cost ledger rows are absent. `VOICE_RECONNECT_RECOVERY=false` still permits the basic reconnect loop (up to eight attempts); it is not a no-retry switch. Monthly budgets, alerts, post-call metering and a manual stopwatch cannot establish a hard spending guarantee. Do not dial until a fail-closed spending mechanism is demonstrated offline.

## Artifacts and scope of changes

Safe evidence:

- `.validation-8d/full-suite.xml` and `.validation-8d-full-suite.log`: complete API run.
- `.validation-8d/recheck.xml`, `.validation-8d/fixtures.xml`: rechecks, including the three corrected setup cases.
- `.validation-8d/simulations.xml` and `.validation-8d/call-control-lifecycle.xml`: passing simulations.
- `.validation-8d-web-unit.log`, `.validation-8d-web-e2e.log`: browser/frontend outcomes.
- `.validation-8d-readonly-audit.json`: allowlisted organization/settings/authentication findings.
- `.validation-8d-logging.json`: synthetic privacy case results, without captured payloads.

This continuation added reusable `scripts/pilot_offline_suite.py`, `scripts/pilot_readonly_audit.py`, `scripts/pilot_logging_verify.py`, two call-control lifecycle tests, this report, and the controlled-call checklist. It adjusted the env-file resolution test prerequisite. No runtime application code or business configuration was changed in this continuation. Existing dirty files were preserved.

Ruff passed for the three new scripts and new lifecycle tests; their compilation checks passed. The frontend suite was rerun with a single worker after its default worker pool stalled; the single-worker run produced the explicit 19-pass/six-failure result above.

**Stop for approval.** Remaining work is to resolve logging leaks, reconcile the two call-control expectations, repair frontend test setup and isolated browser coverage, attest a serving pilot process when authorized, obtain business approval, and validate a hard spending guard. No paid call, deployment or Phase 7 activation is approved.
