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

## Verification
- `cd apps/api && pytest tests/test_auth_endpoints.py -k forgot_token -v`
- Full backend suite: `pytest apps/api/tests/test_auth_endpoints.py apps/api/tests/test_admin_endpoints.py -q` (sanity check nothing else broke)
- Frontend: `cd apps/web && npx tsc --noEmit` for type-check
- Manual: start both dev servers, visit `/login`, click "Forgot your login token?", submit a seeded account's email/mobile, confirm the generic message shows and (with `BREVO_API_KEY`/Twilio-Plivo configured) the new token arrives; confirm the old token now gets 401 on `/auth/login` and the new one logs in.
