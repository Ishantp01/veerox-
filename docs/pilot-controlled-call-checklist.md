# Controlled outbound call checklist — PREPARED, NOT AUTHORIZED

Prepared 21 October 2026. This document does not authorize a call, deployment, credential change, business-configuration write, or Phase 7 activation. Current approved spending is **zero**.

## Release gates

- [ ] Review the separate Phase 8D validation report. Resolve its logging failures and any outstanding application-test failures.
- [ ] Obtain explicit approval for exactly one outbound call to the operator's allowlisted test handset. No real customer, campaign, retry, fallback dial, or second call.
- [ ] Confirm the intended pilot org by its configured ID. Owner must approve the business identity, greeting, permitted actions, and test facts. The audit currently found zero knowledge items, zero scripts, and no published version; no configuration changes are authorized by this checklist.
- [ ] Attest the actual serving process's `/diag/voice-config` values. Keep enhanced dialogue, smart turn-taking, reconnect recovery, and TTS recovery **off** unless separately approved. A test-only flag override inside pytest is not activation.
- [ ] Confirm workers, provider startup registration, and provider configuration changes remain off. Do not change any webhook to perform this test.
- [ ] Verify recording off; transcript logging off; access logs off; nested webhook/provider payload and credential redaction tests all passing. Do not paste raw provider responses into the report.
- [ ] Verify isolated pilot DB/Redis targets and current credentials work. Credential rotation was waived by the user; do not mark it verified or perform rotation.
- [ ] Establish and offline-test the hard spending controls below. **This gate currently fails**: no cost-rate rows and no verified hard per-call monetary gate were found.

## Cost estimate and proposed limits

Plan for **60–90 seconds connected**, 4–6 short caller turns, ending voluntarily before 90 seconds. Proposed provider-side maximum: **120 seconds**, including disconnect margin; ring timeout at most 20 seconds. Neither maximum was configured here. No automatic retry or provider failover for this experiment.

The pilot file currently selects `gpt-realtime-2.1` with text output to `eleven_turbo_v2_5`, plus `gpt-live-transcribe` for partial transcripts. The main Realtime session also requests `whisper-1` transcription. Do not use the repository's default preview-model price to budget this pilot.

Illustrative 90-second planning envelope, **not a measured bill or guaranteed upper bound**:

| Component | Explicit planning assumption | Estimated incremental cost |
|---|---|---:|
| Realtime text input | 10,000–25,000 aggregate billed input tokens across turns, uncached | $0.040–$0.100 |
| Realtime audio input | 1,000–3,000 aggregate billed audio-input tokens, uncached | $0.032–$0.096 |
| Realtime text output | 300–600 billed output tokens | $0.0072–$0.0144 |
| Turbo TTS | 500–1,000 generated characters, including interrupted text | $0.020–$0.040 |
| Partial transcription | 90 seconds submitted | $0.0255 |
| Main-session Whisper transcription | Up to 90 seconds submitted | $0.009 |
| Turn classifier | Reserve; verify actual call count and token usage | $0.005 |
| Plivo domestic leg | 90 seconds at the published India domestic rate | ₹0.57 |

AI subtotal is approximately **$0.14–$0.29**, plus ₹0.57 telephony, before taxes/account-specific adjustments. At a deliberately conservative **planning conversion of ₹100 per USD** (not a claim about the current market exchange rate), that is about **₹15–₹30 before taxes**. Reserve **₹40** as the expected planning allowance. Existing number rental/subscriptions and standing infrastructure are excluded from incremental usage; a new purchase is not authorized.

Sources checked 21 October 2026: [official OpenAI Realtime model pricing](https://developers.openai.com/api/docs/models/gpt-realtime-2.1) lists $4/M input text, $24/M output text and $32/M input audio; [official OpenAI transcription pricing](https://developers.openai.com/api/docs/pricing) lists live transcription at $0.017/minute and Whisper at $0.006/minute; [ElevenLabs API pricing](https://elevenlabs.io/pricing/api) lists Flash/Turbo at $0.04/1,000 characters; [Plivo India pricing](https://www.plivo.com/voice/pricing/in/) lists ₹0.38/minute and 30-second pulses. These public rates do not establish the account's actual contract or available credits. Recheck its route, billing pulse, model, plan, taxes and conversion before approval. Context replay/rebilling, hidden reasoning, interrupted generations, retries, and simultaneous transcription may increase cost beyond this planning envelope.

### Hard ceiling: proposed ₹100 for the entire experiment

No part of this ceiling is currently authorized to spend. A future approval should explicitly name it. The allowed call count is one; attempts and aborted requests count toward the same ceiling.

- [ ] Confirm account-specific tariffs and a conservative INR conversion/tax margin. Allocate provider sub-budgets whose sum, including in-flight requests and margin, is at most ₹100.
- [ ] Enforce admission/reservation before each billable request, with bounds on response output, total responses, TTS characters, transcription duration, retries and reconnect sessions. Metering after completion is not a hard cap; spending alerts and monthly dashboard budgets must not be treated as one.
- [ ] Reserve the worst-case cost of an in-flight generation before starting it. Cancel/close both OpenAI legs and the TTS session when no budget remains. Prevent all tool-triggered outbound calls, messages and paid side effects.
- [ ] Set a provider-side 120-second call limit for this one test, independently of the app. Validate the hangup/finally paths with offline fakes first. No such provider request was made here.
- [ ] Stop accepting new billable work at the earlier of 90 seconds or the allocated soft threshold; use reserved disconnect/in-flight headroom to stay below ₹100. If an enforceable bound cannot be demonstrated, **do not dial**.

The current application does not demonstrate these controls. Implementing/configuring them requires a separate approved change; a manual timer alone cannot certify a hard monetary limit.

`VOICE_RECONNECT_RECOVERY=false` disables enhanced recovery, not the bridge's basic reconnect loop: up to eight reconnect attempts remain possible. Count their billable sessions in the budget and constrain them for the future experiment; do not equate a disabled Phase 7 flag with zero retries.

## Test flow and success criteria

Use invented details only. Capture timing/counters rather than speech content. Reconnect recovery, overlapping tool calls and action deduplication remain offline tests for this call; do not deliberately cause an outage or business action on a paid line.

1. Answer and hear one brief AI disclosure and approved business identity. No invented claims or extra greeting.
2. Ask one short approved test question, then a second question during the agent's answer. Playback must clear within the agreed 1-second measurement target after caller speech detection, with no stale audio resuming. This measures detection-to-clear, not acoustic end-to-end delay.
3. Give a short acknowledgment and leave a brief quiet interval. Count any unwanted reply/cut. No classification-quality claim from a single sample.
4. Ask one final approved question and say goodbye. Tester hangs up by 90 seconds even if automatic end-call behavior is disabled. Confirm provider terminal status by 120 seconds.

Pass criteria: zero duplicate/conflicting responses; zero unnecessary Realtime reconnects; no repeated side effects; zero privacy leaks; no false business promises; first-audio median at most 2.5 seconds over the measured replies and no reply above 5 seconds; one clear interruption with no stale playback; one terminal call state; actual total usage reconciled under ₹100. Latency thresholds are proposed acceptance targets, not claims of observed performance. Record the sample count; do not label a few replies a reliable p95 distribution.

Any privacy leak, wrong recipient/org, unexpected paid tool action, uncontrolled retry, lost spending guard, call continuing beyond its bound, or unsupported business statement fails the test immediately. No second call follows automatically.

## Abort and rollback

1. Tester hangs up immediately. Operator also uses the approved provider hangup path and confirms a terminal call state. Closing the application's stream alone is insufficient because Plivo uses `keepCallAlive`.
2. Stop the isolated pilot process and its test-only tunnel to prevent more requests. Keep workers off. Do not toggle a global Redis kill switch or change production/provider routing as a rollback.
3. Restore only the previously reviewed local pilot code/env snapshot if a local change was separately approved. Do not publish/rollback org configurations or activate flags without approval. This validation made no such changes.
4. Reconcile measured tokens, generated TTS characters, both transcription streams, rounded carrier time, retries and taxes against the reserved ceiling. Retain only sanitized evidence. Stop for review before any further spend.

Approval record: **pending**. Budget guard: **not verified**. Business readiness: **not approved**. This checklist is not an instruction to dial.
