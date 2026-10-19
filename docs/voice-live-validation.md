# Voice live-call validation plan

Status: **prepared, not executed.** No real call, paid API request or provider request has been made for Phase 6.
Everything below needs explicit approval first (calls cost money and ring real phones).

## Ground rules

- Staging environment only, a **test org** that has no real customers, and phones you hold.
- One flag change at a time (order in `voice-rollout-plan.md`). Record the flag set with each call
  (`GET /diag/voice-config`).
- Every call gets a row in the results table below. A call is judged by the automated checks **and** a person
  listening. Passing one call proves little; the latency checks in particular need many turns.

## Before the first call

1. `GET /diag/voice-config` shows no `error` issues for the flags you will use.
2. `python scripts/validate_migrations.py <staging-db-url> --yes-this-is-not-production` passed on a disposable DB.
3. Run the two paid probes once, with approval, and record the results:
   - `scripts/probe_realtime_cancel.py` – does `response.cancel` keep the Realtime session open? (gates `VOICE_SAFE_CANCEL`)
   - `scripts/probe_hangup.py plivo hangup <uuid of a test call>` – does the provider API really end a call?
     (and `delete-recording` for the retention flag; Twilio the same)

## Scenarios

The machine-readable list is `scripts/voice_validation_scenarios.json` (19 entries: the 17 areas in the brief, with
recording consent / retention / metering split for clarity). Each has the tester's script, the expected result, the
flags needed, the automated checks and the listening checks.

| ID | Area | Needs flags |
|----|------|-------------|
| S01 | Inbound English | – |
| S02 | Outbound Hindi | – |
| S03 | Hinglish | – |
| S04 | Single interruption | SAFE_CANCEL |
| S05 | Rapid interruptions | SAFE_CANCEL |
| S06 | Safe cancel keeps session open | SAFE_CANCEL |
| S07 | Prewarm + non-blocking finish | PREWARM, NONBLOCKING_TTS_FINISH |
| S08 | AI disclosure + greeting cache | GREETING_AI_DISCLOSURE, GREETING_AUDIO_CACHE |
| S09 | Voice profile / normalisation | – |
| S10 | Objection handling | – |
| S11 | Corrections | – |
| S12 | Appointment confirmation | RECONNECT_RECOVERY |
| S13 | Agent ends the call | END_CALL_ENABLED |
| S14 | Phrases that must not end the call | END_CALL_ENABLED |
| S15 | TTS failure recovery | TTS_RECOVERY |
| S16 | Realtime reconnection | RECONNECT_RECOVERY |
| S17a/b/c | Recording consent, retention, metering | METERING_V2, RETENTION_* |

## After each call

```
python scripts/voice_postcall_verify.py S04 --logs api.log --call-uuid <uuid> \
    --conversation conv.json --appointments appts.json --usage usage.json
```

Output is `PASS / FAIL / MANUAL / SKIPPED` per check. `SKIPPED` means the data needed was not provided or the event never
happened (for example no interruption occurred) – it is **not** a pass. `MANUAL` checks are listening judgements: write the
score in the table. Exit status is 1 if anything failed.

Latency: `docker logs veerox-api-1 2>&1 | python scripts/voice_latency_report.py` after at least a few hundred turns, and
`GET /diag/voice-latency` for the rolling per-process numbers. Report `n` with every percentile. Segments measured:
speech end → first audio sent, TTS connect (`tts_connect_ms`), decision latency, interruption → playback cleared,
cancel → `response.done`, reconnect recovery (`reconnect_recovery_ms`), hangup (`hangup_ms`). "First audio sent" is when
audio was handed to the carrier, not when the caller heard it.

## Voice quality comparison (Turbo v2.5 / Flash v2.5 / OpenAI native)

1. Collect 15–20 sentences covering Hindi, Hinglish, English, Indian names, amounts, dates, a long and a short answer.
2. `python scripts/voice_quality_rate.py sheet prompts.txt --seed 7 --out sheet.json` writes a blind sheet.
3. Generate the clips for each variant (paid; needs approval; `scripts/voice_compare.py`), name them by the sheet's labels.
4. At least 3 listeners score every clip 1–5 on naturalness, clarity, Hindi pronunciation, name pronunciation, pacing.
5. `python scripts/voice_quality_rate.py report sheet.json ratings.json`. The tool names a leader only with ≥3 raters,
   ≥12 scores per variant and a gap of ≥0.3 points; otherwise it prints why it cannot conclude. **No variant has been
   rated, and none is recommended here.**

## Results table (to be filled in)

| Date | Scenario | Flags | Call id | Auto checks | Listening scores | Notes / defects |
|------|----------|-------|---------|-------------|------------------|-----------------|
| | | | | | | |

## Open questions only live calls can answer

- Does `response.cancel` close the session on gpt-realtime-2.1? (probe + S04–S06)
- Does deleting and re-inserting items on a text-only response keep the Realtime conversation valid? (S04 with
  `VOICE_TRIM_INTERRUPTED_CONTEXT`; listen for the agent repeating or skipping content afterwards)
- Do Plivo and Twilio hangup / recording-delete endpoints behave as coded?
- Are ElevenLabs `voice_settings` keys accepted for the chosen model, and is the first-audio timeout (6 s default) too
  aggressive for the p99 you measure?
- Does the cached greeting play correctly and without clipping?
