# Phase 8J: replay-state design review (22 October 2026)

Nothing was initialised, rotated, created or called. Status of the pilot is unchanged: **BLOCKED, no call authorised.**
Evidence: `test_pilot_8j_replay.py` (21, real Lua semantics through fakeredis; run with `.venv`, Python 3.12 — the system Python 3.13 has no fakeredis),
`test_pilot_8j_initializer_guard.py` (10), `test_voice_webhook_signature.py` + `test_pilot_8i_gate.py` (67), all passing. Lua was not executed against Upstash's write path.

## 1. What the design does and does not guarantee

| Question | Answer |
|---|---|
| Can nonce storage grow without limit? | No. One set, hard cap 100,000 accepted nonces (about 10-15 MB, an estimate), then every new delivery is refused (`nonce_capacity`). No per-request keys, no TTL. Stream ownership is one hash capped at 10,000 entries, entries removed on clean close. A pilot call adds about one nonce. |
| Can nonce records safely expire? | Not on their own. Expiry re-admits an already-accepted signed request. It is only safe if freshness can be proved some other way (below). |
| Does Plivo give enough freshness information? | No. V3 signs URL + POST parameters + a random nonce. The nonce is not a timestamp, and the documented callback parameters contain no time field. A delayed request that was never accepted before cannot be told from a new one. |
| Does stream ownership survive a worker crash? | Yes, indefinitely, by design (a timeout would let a paused old worker overlap its replacement). It only blocks that one org+call id, which is dead after a crash; new calls are unaffected. Leaked entries count toward the 10,000 cap. **There is no tool to clear a stale owner**; that is a gap. |
| Can a legitimate reconnect recover? | A dropped telephony socket releases after cleanup, and the same unexpired token (300 s) may reconnect sequentially. A reconnect of the OpenAI leg inside one stream keeps ownership. After a crash, no. A second simultaneous connection is refused. |
| Does it work across API instances? | The ledger and ownership live in Redis with atomic Lua, so yes, if every instance uses the same Redis **and the same `SECRET_ENCRYPTION_KEY`** (the namespace is derived from it, falling back to `ADMIN_TOKEN`). Different keys mean disjoint ledgers. Other pilot guards are per-process (the one-call counter, the pre-connected AI session), so the pilot must be a single process. |

Not claimed: that replay is impossible. Residual: a never-accepted captured request stays valid until the signing token changes; a stream token is a
bearer credential for sequential reuse until it expires; Redis data loss or restoring an old snapshot loses nonces while keeping the marker; and every
Redis fault refuses service (safe, but it costs the call).

## 2. Isolation options

| | A. Dedicated Plivo pilot subaccount | B. Initialise with the existing shared credentials | C. Rotate the shared production token |
|---|---|---|---|
| Security risk | Lowest. Requests captured earlier were signed with the main token, which the pilot would no longer accept, so the ledger starts clean without rotating anything. | Requests captured before the ledger existed (earlier test calls, if any record survives) can each be replayed once, and the response carries a fresh stream token. Requires an explicit waiver; none exists today. | Lowest for the pilot, but the new token must reach every consumer. |
| Production impact | None, provided the production number is not moved (a number belongs to one account; moving it would disrupt production). | None. | High: every service holding the old token fails until updated (EC2 env, GitHub secrets, the main `.env`). |
| Setup | Create the subaccount with main credentials; it needs its **own number** (India numbers need KYC, so days) or a verified caller ID if Plivo allows one for India (unchecked); put the new auth id/token/number in `.env.pilot` and the pilot org's number row; then initialise with all three confirmations true. | Add a narrow, logged, time-boxed waiver to the initialiser (needs your approval), then initialise. | Rotate in the console, update every consumer, then initialise. |
| Cost | Number rental and KYC time. **Charges still roll up to the main account's balance**, so it does not isolate spending. | None. | None, plus outage risk. |
| Rollback | Delete the subaccount (its numbers return to the main account); restore the `.env.pilot` lines. | Delete the two replay keys only after stopping the pilot **and** after the token has changed; deleting them while the token is still accepted re-opens replay. | Cannot restore the old token. |

**Option D (not requested, optional):** have the dial request put a random single-use code in the answer URL (Plivo signs the whole URL, so the code is
covered) and store it with a short TTL; the answer webhook consumes it. Freshness then comes from our own state, the ledger can expire, and no
rotation attestation is needed. No provider change; new security code in the outbound-call, dialer and AI-callback paths, with tests.

## 3. The initialiser (`scripts/pilot_security_initialize.py`)

| Check | Finding |
|---|---|
| Targets only the pilot Redis | **Gap found and fixed.** It only refused a Redis equal to the main `.env`'s. A shell `REDIS_URL` beats the env file, so any other Redis passed (demonstrated). It now also requires `ENVIRONMENT`, `REDIS_URL`, `SECRET_ENCRYPTION_KEY`, `ADMIN_TOKEN` and `PLIVO_AUTH_TOKEN` in force to equal `.env.pilot`, and the Redis to differ from the main one. The read-only status script got the same Redis check. |
| Cannot modify production Redis | Production runs in mode `off` and never touches these keys; the initialiser can only write the Redis named by `.env.pilot`. It cannot know the production Redis address on its own, so the file-equality rule is the guard. |
| Idempotent, never overwrites | Yes: one atomic Lua call refuses if either key exists (including a half-present state); tested. |
| Cannot silently bypass required checks | The three flags are required on the command line (verified: running without them exits before any connection), but they are attestations. The script cannot verify rotation and leaves no record of who asserted what. |
| Waiver | None exists. Passing the rotation flag without rotating would be a false statement, so I did not use it. |

## 4. Recommendation

Safest practical route for one call without touching production, in order:
1. Rotate the pilot Neon and Upstash passwords (still unrotated).
2. Take **Option B with a narrow waiver for this single call**: approve a small change that adds an explicit, reasoned, logged, time-boxed waiver in place of the rotation attestation (the waiver expires, after which the ledger refuses until a real new epoch exists), then initialise the isolated pilot Redis. Zero production impact, zero cost; the accepted risk is only the one-time replay of any signed request captured before today.
3. Before any further calls, move to **Option A** (and consider D). Do not choose C.
4. Add a small operator tool to clear one stale stream owner after proving its worker is dead.
5. Still required before authorisation: spending confirmations (OpenAI, ElevenLabs, Plivo), the handset confirmation and written authorisation for exactly one attempt.
