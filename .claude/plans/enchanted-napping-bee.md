# Forgot login token (email or mobile, via Brevo/SMS)

## Context

Veerox's dashboard login (`apps/api/routers/auth.py`) has no password — each `AccountUser` has one permanent random token that IS the credential. Today the *only* recovery path is admin-gated (`POST /team/members/{id}/regenerate-token`, `POST /billing/{org}/regenerate-admin-token`): an org admin or platform admin regenerates a teammate's token on their behalf. There is no self-service "I lost my token" flow, and no email-sending capability exists anywhere in the codebase yet (SMS exists via Plivo/Twilio failover, used today to text a token on signup).

This adds a self-service "Forgot your token?" flow: from the login page, a user enters their email or mobile number, the backend issues a fresh token and immediately invalidates their existing sessions (mirroring the existing regenerate-token pattern, just unauthenticated and self-triggered), and delivers the new token by email (via Brevo) or SMS (via the existing Plivo/Twilio failover), matching whichever identifier they entered.

Since this is a public, unauthenticated endpoint, it must not leak whether a given email/mobile has an account (user enumeration) — it always returns the same generic response.

## Backend

**1. Brevo email client — new `apps/api/channels/email/` package**
- `apps/api/channels/email/__init__.py` (empty, matches `channels/voice/__init__.py` / `channels/whatsapp/__init__.py`)
- `apps/api/channels/email/brevo_client.py`, same shape as `apps/api/channels/voice/plivo_client.py`: module-level `httpx.AsyncClient`, `_BREVO_BASE = "https://api.brevo.com/v3"`.
  - `is_configured() -> bool` — `bool(settings.brevo_api_key)`
  - `async def send_email(to_email: str, subject: str, html_content: str, to_name: str | None = None) -> dict[str, Any]` — `POST /smtp/email` with header `api-key: settings.brevo_api_key`, body `{"sender": {"name": settings.brevo_sender_name, "email": settings.brevo_sender_email}, "to": [{"email": to_email, "name": to_name or to_email}], "subject": subject, "htmlContent": html_content}`. `raise_for_status()`, log warning + reraise on `httpx.HTTPError`, log info + return json on success — same try/except/log shape as `plivo_client.send_sms`.

**2. Config — `apps/api/config.py`**
Add near the other provider blocks:
```python
# Brevo transactional email (forgot-token delivery only, for now).
brevo_api_key: str | None = None
brevo_sender_email: str = "no-reply@veerox.ai"
brevo_sender_name: str = "Veerox"
```

**3. Schemas — `apps/api/schemas/auth.py`**
```python
class ForgotTokenIn(BaseModel):
    identifier: str  # email address or E.164 mobile number

class ForgotTokenOut(BaseModel):
    message: str
```

**4. Router — `apps/api/routers/auth.py`**
New endpoint, rate-limited like `admin.py`'s `outbound_whatsapp`/`outbound_call` (`@limiter.limit(...)`, needs a `request: Request` param):
```python
@router.post("/forgot-token", response_model=ForgotTokenOut)
@limiter.limit("5/minute")
async def forgot_token(request: Request, payload: ForgotTokenIn, db: DbDep, redis: RedisDep) -> ForgotTokenOut:
```
Logic:
- `identifier = payload.identifier.strip()`; `is_email = "@" in identifier`.
- Look up `AccountUser` by `func.lower(AccountUser.email) == identifier.lower()` if `is_email`, else `AccountUser.mobile == identifier`.
- If a match exists and `is_active`: generate a new token (`generate_login_token()`/`hash_token()`, same as `team.py::regenerate_member_token`), commit, `invalidate_user_sessions(redis, account_user.id)`, then best-effort deliver:
  - email → `brevo_client.send_email(account_user.email, "Your new Veerox login token", f"<p>Your new login token: <b>{login_token}</b></p><p>Your previous token no longer works.</p>")`
  - mobile → `voice_failover.send_sms(account_user.mobile, f"Your new Veerox login token: {login_token}")`
  - Wrap delivery in `try/except httpx.HTTPError` → `logger.warning(...)`, same as `provision_org`'s SMS best-effort. Delivery failure must NOT change the response or status code (would leak account existence).
- Whether or not a match was found, always `return ForgotTokenOut(message="If an account matches, a new login token has been sent.")`.
- Import additions: `Request` from fastapi, `func` from sqlalchemy (or reuse existing `select`), `from apps.api.channels.email import brevo_client`, `from apps.api.rate_limit import limiter`, `ForgotTokenIn, ForgotTokenOut` from schemas.

**5. Tests — `apps/api/tests/test_auth_endpoints.py`**
Add cases (mock `brevo_client.send_email` and `voice_failover.send_sms` with `monkeypatch`/`AsyncMock`, following whatever mocking convention this test file already uses for `provision_org`'s SMS):
- Existing email → 200 generic message, mocked `send_email` called once, old token now 401s on `/auth/login`, new token (captured from the mock call args) logs in successfully.
- Existing mobile → 200 generic message, mocked `send_sms` called once, same before/after token check.
- Unknown identifier → 200, same generic message, neither mock called.
- Inactive account → 200, same generic message, no token change (treat like "not found").

## Frontend

**1. API hook — `apps/web/src/lib/hooks/useAuthApi.ts`**
```ts
/** POST /auth/forgot-token — always resolves with a generic message; never reveals whether the identifier matched an account. */
export function forgotToken(identifier: string): Promise<{ message: string }> {
  return apiFetch("/auth/forgot-token", {
    method: "POST",
    body: JSON.stringify({ identifier }),
  });
}
```

**2. New page — `apps/web/src/app/(auth)/forgot-token/page.tsx`**
Same visual shell as `apps/web/src/app/(auth)/login/page.tsx` (logo block + `rounded-2xl border border-white/10 bg-white/[0.04] ...` card, reuses `Button`/`Input`/`Label` from `@/components/ui`), rendered inside the existing `(auth)/layout.tsx` (no sidebar):
- One field: "Email or mobile number" (zod: non-empty string).
- Submit → `forgotToken(value)`. On both success and any thrown error, show the same generic confirmation copy ("If an account matches, a new login token has been sent.") — don't distinguish network/API errors from "not found" for the same enumeration reason as the backend; only a genuinely broken submit (e.g. empty field) should block submission client-side.
- A link back to `/login` ("Back to sign in").

**3. Login page — `apps/web/src/app/(auth)/login/page.tsx`**
Add a `next/link` under the form (near the existing "Don't have a token? Ask your organization's admin." line):
```tsx
<Link href="/forgot-token" className="text-xs text-primary-400 hover:text-primary-300">Forgot your login token?</Link>
```

## Env / setup note for the user
`BREVO_API_KEY` must be added to `.env` (plus optionally `BREVO_SENDER_EMAIL`/`BREVO_SENDER_NAME` to override the defaults) for email delivery to actually send — without it, `brevo_client.is_configured()` is false the same way Plivo/Twilio are today when unset, and I'll skip the call rather than raise, so the endpoint still returns its generic success response instead of erroring.

## Documentation deliverable — `tokensending.md`

Alongside the code changes, create `tokensending.md` at the repo root documenting this feature in full detail — every change made in this session, including edge cases, so it stands alone as a reference. Structure:
- **Overview**: what "forgot token" does and why it exists (no password system, prior recovery was admin-only — see Context above).
- **Backend changes**: every new/edited file (`apps/api/channels/email/__init__.py`, `apps/api/channels/email/brevo_client.py`, `apps/api/config.py`, `apps/api/schemas/auth.py`, `apps/api/routers/auth.py`, `apps/api/tests/test_auth_endpoints.py`) with what changed and why.
- **Frontend changes**: every new/edited file (`useAuthApi.ts`, `forgot-token/page.tsx`, `login/page.tsx`) with what changed and why.
- **Request/response contract**: exact `POST /auth/forgot-token` request body and response body, including the 200-generic-response-always behavior.
- **Edge cases enumerated explicitly**, each with its exact resulting behavior:
  - Identifier matches no account → generic success, no email/SMS sent.
  - Identifier matches an `is_active=False` account → treated as not-found (generic success, no send, no token change).
  - Identifier is an email that matches an account with no `mobile`, or a mobile that matches an account fine either way (email/mobile are independent lookups, not "try both").
  - Email matches but Brevo isn't configured (`BREVO_API_KEY` unset) → token still regenerated and old sessions still invalidated (this is the same "recovery" mutation regardless of delivery), but no email actually sent — user is silently unable to retrieve it. Call this out as a known limitation.
  - Mobile matches but neither Plivo nor Twilio is configured → same as above for SMS.
  - Email/SMS provider configured but the send call itself fails (`httpx.HTTPError`) → same outcome: token already rotated before the send attempt, failure is only logged, response is unchanged. Call out that this means a failed delivery still invalidates the user's old sessions/token — a real (accepted) tradeoff of doing the rotate-then-send in that order, matching `provision_org`'s existing best-effort-SMS precedent.
  - Rate limiting: `5/minute` per IP (`slowapi`, keyed by remote address per `apps/api/rate_limit.py`) → 429 on the 6th request in a minute from the same IP, independent of which identifiers were tried.
  - Multiple accounts sharing the same mobile number (schema doesn't enforce uniqueness on `mobile`, only on `email`/`token_hash`) → query returns whichever row the DB happens to order first; document this as an existing ambiguity, not newly introduced.
  - Case sensitivity: email lookup is case-insensitive (`func.lower(...)`), mobile lookup is exact-string (no normalization of spaces/dashes/leading `0` vs `+country code`).
  - Frontend: identical generic message is shown whether the backend returned success-with-match, success-without-match, or even a network error reaching the API at all (documented as intentional, to avoid a client-visible timing/error-shape enumeration channel) — note the one exception being client-side empty-field validation, which never reaches the network.
- **Env vars required**: `BREVO_API_KEY` (required for email delivery), `BREVO_SENDER_EMAIL`/`BREVO_SENDER_NAME` (optional overrides) — plus a reminder that SMS delivery reuses whatever Plivo/Twilio credentials are already configured, no new SMS-specific setup needed.
- **Testing performed**: list the exact test commands run and their results (filled in after implementation, not written speculatively).

This file is a one-time snapshot of this session's work, not living documentation — write it after the code changes are made and verified, so it reflects what was actually built rather than what was planned.

## Verification
- `cd apps/api && pytest tests/test_auth_endpoints.py -k forgot_token -v`
- Full backend suite: `pytest apps/api/tests/test_auth_endpoints.py apps/api/tests/test_admin_endpoints.py -q` (sanity check nothing else broke)
- Frontend: `cd apps/web && npx tsc --noEmit` for type-check
- Manual: start both dev servers, visit `/login`, click "Forgot your login token?", submit a seeded account's email/mobile, confirm the generic message shows and (with `BREVO_API_KEY`/Twilio-Plivo configured) the new token arrives; confirm the old token now gets 401 on `/auth/login` and the new one logs in.
