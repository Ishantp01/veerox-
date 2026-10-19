# Voice compliance and privacy review

This is an engineering review of what the code does. **It is not legal advice and the tests do not establish legal
compliance.** Items marked ⚖ need a decision from the business owner or counsel before the related behaviour is
used with real callers.

## What the system does today

| Area | Behaviour | Verified |
|------|-----------|----------|
| AI disclosure | With `VOICE_GREETING_AI_DISCLOSURE` the greeting states it is an AI assistant (checked by `has_ai_disclosure`). Default off. | unit tests; not by ear |
| Recording default | An org with no policy records **every** call with **no spoken notice** (the long-standing behaviour, deliberately unchanged). | tests |
| Recording modes | Per org and per calling-code region: `always`, `notice` (spoken line), `consent` (not recorded unless the caller clearly agrees), `off`. Region = longest matching prefix of the remote number. | tests |
| Consent | The model reports the answer with a tool; no answer = no recording; the answer is tagged on the conversation. The greeting cache is bypassed in consent mode so the question is always asked. | tests; live behaviour of the model unverified |
| Retention | `retention_days` per org clears our stored recording reference. With `VOICE_RETENTION_DELETE_PROVIDER_AUDIO` it first deletes the provider-hosted file and clears the reference only after the provider confirms (or says it is already gone). Both flags default off. | tests with mocked HTTP; **provider endpoints unverified live** |
| Transcripts | Conversation messages are stored in the database. **No retention or deletion exists for them.** | code review |
| Log privacy | `LOG_PRIVACY_ENABLED` (default on) masks phone numbers and redacts OTP/card/ID/email patterns in logs; `LOG_TRANSCRIPTS` (default **on**) still logs caller/agent text. | tests |
| Third parties | Audio and text go to OpenAI (Realtime, transcription), ElevenLabs (text for TTS) and Plivo/Twilio. | – |
| Isolation | Policies, credentials, usage and retention are org-scoped; tests cover cross-org cases. | tests |

## Items needing a decision ⚖

1. **Default recording with no notice.** Existing orgs keep recording every call silently. Whether that is acceptable
   depends on where each org's callers are (two-party-consent regions, India's data-protection rules, sector rules).
   Recommendation: each org chooses and records a policy before the pilot; consider making `notice` the default for
   *new* orgs.
2. **Consent semantics.** What counts as a clear yes, in which languages, and whether an unanswered question may be
   treated as "no" (current behaviour) or ends the recording attempt.
3. **AI disclosure wording and timing.** Required wording and whether it must precede any other speech or recording.
4. **Transcript storage.** Transcripts are kept indefinitely and may contain personal data the caller speaks aloud.
   Needs a retention period, a deletion path (including on request from the caller), and a lawful basis.
5. **`LOG_TRANSCRIPTS=true` by default.** Transcripts also land in application logs (retention of Docker logs is
   unmanaged). Recommendation: set it to `false` in production unless debugging; confirm log retention.
6. **Provider-side copies.** Plivo/Twilio keep recordings and call metadata under their own retention; OpenAI and
   ElevenLabs processing/retention terms and the data-processing agreements, region of processing and zero-retention
   options need review.
7. **Outbound calling rules.** Do-not-call registry checks, permitted calling hours and consent for automated calls are
   not enforced by this phase's code.
8. **Cross-border transfer.** Voice data leaves the country to US-hosted providers; check the org's obligations.
9. **Deletion verification.** Provider deletion is coded to the documented endpoints but has never been run against a
   real account; until `probe_hangup.py … delete-recording` is run, treat deletion as unproven.
10. **Redaction coverage.** The redaction is pattern-based and Indian formats (Aadhaar, PAN, UPI ids) should be reviewed
    by someone who knows the data; spoken digits ("nine eight seven…") are not caught.

## What engineering can do next without a legal decision

- Set `LOG_TRANSCRIPTS=false` in staging and verify support can still debug from metrics and ids.
- Add a transcript retention job once the period is decided (same pattern as recordings).
- Add an admin-visible record of each org's policy and last purge run.
