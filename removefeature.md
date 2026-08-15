# Removed features: Help Desk Chatbot, Social Media Links, SMS notification text & monthly billing periods

Date removed: 2026-08-14 (sections 1–3), 2026-08-16 (section 4)

Sections 1–3 were removed from user-visible UI only (Settings page tabs,
navbar/dashboard chrome, and — for SMS — a status line in a dialog). All
backend routes, DB models, and the underlying React components/hooks/side
effects were left intact and untouched so any of them can be re-enabled by
wiring the UI back up — no backend or data work needed.

**Section 4 is different in kind** — read its own note before assuming the
paragraph above applies to it. Monthly billing periods were removed from the
*backend and the database*, not just the UI: a worker file was deleted, a
schema migration ran, and plan-limit semantics changed. Restoring it is real
work, not a re-import.

Update (still 2026-08-14, two follow-ups): both editors were also pulled from
the **Billing page** (`/billing`), where they'd remained mounted for platform
admins even after the Settings-page tabs and navbar widget/icon row were
removed. First the Help Desk script editor was dropped (leaving
`<SocialLinksPanel />` alone), then the Social Links editor was dropped too —
so `/billing` no longer renders either `HelpDeskScriptPanel` or
`SocialLinksPanel`/`PlatformSettingsPanel` at all now.

Every spot the UI was disconnected from also has a short inline code comment
pointing back to this file, so anyone reading `layout.tsx`, `topbar.tsx`,
`settings/page.tsx`, or `billing/page.tsx` lands here directly instead of
having to go looking:
- `apps/web/src/app/(dashboard)/layout.tsx` — comment where `<HelpDeskWidget />` was rendered.
- `apps/web/src/components/layout/topbar.tsx` — comment above the imports and where `<SocialLinksRow />` was rendered.
- `apps/web/src/app/(dashboard)/settings/page.tsx` — comment below `TABS` where `PLATFORM_TABS` used to be defined.
- `apps/web/src/app/(dashboard)/billing/page.tsx` — comment where both `<HelpDeskScriptPanel />` and `<SocialLinksPanel />` (originally the combined `<PlatformSettingsPanel />`) used to render.
- `apps/web/src/components/organizations/new-org-dialog.tsx` — comment where the SMS status line used to render, on the org-creation success screen.
- `apps/api/main.py` — comment in `lifespan` where `run_billing_expiry_checker()` used to be started (section 4).
- `apps/web/src/components/billing/usage-warning-banner.tsx` — comment in the component docstring where the renewal-reminder case used to be (section 4).

---

## 1. Help Desk Chatbot (floating widget, bottom-right on every dashboard page)

### What was disconnected
- **`apps/web/src/app/(dashboard)/layout.tsx`**
  Removed `import { HelpDeskWidget } from "@/components/helpdesk/help-desk-widget"`
  and the `<HelpDeskWidget />` render inside `<DashboardShell>`. This is what made
  the floating chat bubble appear on *every* authenticated dashboard page.
- **`apps/web/src/app/(dashboard)/settings/page.tsx`**
  Removed the superuser-only "Help Desk Bot" tab (`PLATFORM_TABS`, the
  `tab === "helpdesk"` block, and the `HelpDeskScriptPanel` import) that let a
  platform admin edit the chatbot's system prompt from the Settings page.

### What still exists (untouched, just unused)
- `apps/web/src/components/helpdesk/help-desk-widget.tsx` — the full floating
  widget component (drag-to-reposition launcher bubble + chat panel).
- `apps/web/src/lib/hooks/useHelpDesk.ts` — `useHelpDeskChat()` mutation hook,
  calls `POST /helpdesk/chat`.
- `apps/web/src/components/billing/platform-settings-panel.tsx` —
  `HelpDeskScriptPanel` component (script editor) itself is untouched and
  fully working, just no longer mounted anywhere. It used to also be reachable
  from the Billing page (`apps/web/src/app/(dashboard)/billing/page.tsx`) via
  `<PlatformSettingsPanel />`, but that was replaced with `<SocialLinksPanel />`
  directly so the Help Desk script editor no longer shows up there either.
- Backend, fully intact:
  - `apps/api/routers/helpdesk.py` — `POST /helpdesk/chat`
  - `apps/api/schemas/helpdesk.py` — request/response schemas
  - `apps/api/core/prompts.py` — `HELP_DESK_SYSTEM_PROMPT` default
  - `apps/api/db/models/platform_settings.py` — `PlatformSettings.help_desk_script`
    column (shared table with social links, see below)
  - `apps/api/routers/billing.py` — `GET/PATCH /billing/platform-settings`
    (superuser-gated) used by `HelpDeskScriptPanel`
- Types: `HelpDeskMessage` in `apps/web/src/lib/types.ts`

### How to re-add
1. In `apps/web/src/app/(dashboard)/layout.tsx`, re-import `HelpDeskWidget`
   and render `<HelpDeskWidget />` inside `<DashboardShell>` again.
2. (Optional) In `apps/web/src/app/(dashboard)/settings/page.tsx`, restore the
   `"helpdesk"` tab — re-add `Sparkles` icon import, the `PLATFORM_TABS` array,
   widen `Tab` to include `"helpdesk"`, merge `PLATFORM_TABS` into the tab list
   for `user?.is_superuser`, and re-add the `tab === "helpdesk"` render block
   using `HelpDeskScriptPanel` (already exported from
   `platform-settings-panel.tsx`, no changes needed there).
3. (Optional) In `apps/web/src/app/(dashboard)/billing/page.tsx`, re-import
   and render `<PlatformSettingsPanel />` (restores both editors together)
   from `@/components/billing/platform-settings-panel`, or import just
   `HelpDeskScriptPanel` if you only want this one back.

---

## 2. Social Media Links (icon row in the topbar/navbar)

### What was disconnected
- **`apps/web/src/components/layout/topbar.tsx`**
  Removed the `SocialLinksRow` component (rendered the row of social icons —
  Twitter/X, LinkedIn, Instagram, Facebook, YouTube, WhatsApp, Website — next
  to the org badge), its `<SocialLinksRow />` render call, and the now-unused
  `useSocialLinks` / `SOCIAL_META` imports.
- **`apps/web/src/app/(dashboard)/settings/page.tsx`**
  Removed the superuser-only "Social Links" tab (part of the same
  `PLATFORM_TABS` removal above) that let a platform admin edit the links.

### What still exists (untouched, just unused)
- `apps/web/src/lib/hooks/useSocialLinks.ts` — `useSocialLinks()` query hook,
  calls `GET /billing/social-links`.
- `apps/web/src/lib/social-links.ts` — `SOCIAL_META`, the fixed key → icon/label
  map (`twitter`, `linkedin`, `instagram`, `facebook`, `youtube`, `whatsapp`,
  `website`) used by the removed `SocialLinksRow`.
- `apps/web/src/components/billing/platform-settings-panel.tsx` —
  `SocialLinksPanel` component (the editor form, `SOCIAL_FIELDS`) itself is
  untouched and fully working, just no longer mounted anywhere (it was also
  pulled from the Billing page — see update note above).
- Backend, fully intact:
  - `apps/api/routers/billing.py` — `GET /billing/social-links` (public,
    read-only) and the same `PATCH /billing/platform-settings` used to write
    `social_links`
  - `apps/api/db/models/platform_settings.py` — `PlatformSettings.social_links`
    column (same row as `help_desk_script`, single-row table)
  - `apps/api/schemas/billing.py` — schemas
- Types: `SocialLinks`, and the `social_links` field on `PlatformSettings`, in
  `apps/web/src/lib/types.ts`
- Query keys: `queryKeys.socialLinks()` / `queryKeys.platformSettings()` in
  `apps/web/src/lib/query.ts` — unchanged.

### How to re-add
1. In `apps/web/src/components/layout/topbar.tsx`, re-add:
   ```ts
   import { useSocialLinks } from "@/lib/hooks/useSocialLinks";
   import { SOCIAL_META } from "@/lib/social-links";
   ```
   and the `SocialLinksRow` component (see git history for the original
   implementation — it read `useSocialLinks().data?.social_links`, filtered
   out blank entries, and rendered one icon link per configured platform),
   then render `<SocialLinksRow />` back inside the topbar's right-hand
   `<div className="flex shrink-0 items-center gap-3">`.
2. (Optional) Restore the `"social"` Settings tab the same way as the
   Help Desk tab above, using the already-intact `SocialLinksPanel`.
3. (Optional) In `apps/web/src/app/(dashboard)/billing/page.tsx`, re-import
   and render `<PlatformSettingsPanel />` (restores both editors together)
   or just `SocialLinksPanel` if you only want this one back — same as
   step 3 under Help Desk above, since both live in the same file/import.

---

## 3. SMS notification text (New organization dialog, org-creation success screen)

SMS itself is not an optional, toggleable feature the way the two above are —
it's a best-effort side effect that already fires unconditionally inside
`POST /auth/provision-org` (platform-admin-only org creation), with no
separate UI trigger of its own. So there's nothing to unmount; the only
UI-facing surface was a status line reporting whether that background SMS
send succeeded. That line is what was removed.

### What was disconnected
- **`apps/web/src/components/organizations/new-org-dialog.tsx`**
  Removed the `<p>` that read either "The token was also texted to the
  mobile number provided." or "Could not send the token by SMS — share it
  manually.", shown on the success screen right after creating an org. The
  screen still shows the email + login token exactly as before.

### What still exists (untouched, just unused)
- `apps/api/routers/auth.py` (`provision_org`) — still calls
  `voice_failover.send_sms(...)` with the login token exactly as before; the
  SMS still actually gets sent (or attempted) on every org creation.
- `apps/api/channels/voice/failover.py` — `send_sms()` (tries Plivo, then
  Twilio) and `is_sms_configured()`, unchanged.
- `apps/api/channels/voice/plivo_client.py` / `twilio_client.py` —
  `send_sms()` provider calls, unchanged.
- `apps/web/src/lib/hooks/useAdminOrgs.ts` — `ProvisionOrgResult.sms_sent`
  is still returned by the API and still typed on the frontend; the dialog
  just no longer reads it.
- `apps/api/schemas/auth.py` — `ProvisionOrgOut.sms_sent`, unchanged.
- The "Admin mobile number" field in the dialog is untouched and still
  required — it's still the number the backend SMS's the token to, and is
  also stored on the account regardless of SMS, so it was left in place.

### How to re-add
1. In `apps/web/src/components/organizations/new-org-dialog.tsx`, inside the
   `result ? (...)` block, re-add a `<p>` between the "Give both of these
   to…" paragraph and the Email `<Label>` that branches on `result.sms_sent`
   (see git history around 2026-08-14 for the exact original wording).

---

## 4. Monthly billing periods (30-day plan expiry, calendar-month usage reset)

Date removed: 2026-08-16

Billing moved from a monthly subscription-shaped model to **recharge-based
credits**. A plan is now bought outright: you get its included call minutes
and WhatsApp messages, they're consumed until they run out, and the only
thing that restores them is buying a plan again. Nothing expires on a date
and nothing resets on the 1st of the month.

> **Unlike sections 1–3, this touched the backend and the database.** A
> worker file was deleted, `plans` columns/JSON keys were renamed by
> migration `e7a1c9b2d4f3`, and an API response field was dropped. Code and
> schema must move together — the deployed API will 500 on billing routes if
> it runs the pre-migration code against the migrated DB, or vice versa.

### What was removed

- **`apps/api/workers/billing_expiry.py` — file deleted.** Ran hourly and
  flipped any org past its `period_end` to `billing_status: "past_due"`
  (plus a 3-day-out reminder log). With no time-based expiry there is
  nothing for it to do.
- **`apps/api/main.py`** — `run_billing_expiry_checker()` import and its
  `asyncio.create_task(...)` line in `lifespan`, plus its entry in the
  `background_tasks` tuple.
- **`apps/api/routers/billing.py`**
  - `_PERIOD_DAYS = 30` and the `timedelta` import.
  - `_activate_paid_payment` no longer sets `payment.period_end` to
    `now + 30 days` — it writes `None`. `period_start` still records when
    the recharge landed.
  - `GET /billing/status` no longer returns `current_period_end`; it
    returns `last_recharge_at` (from `BillingPayment.period_start`)
    instead, which is informational only. The query now orders by
    `period_start` rather than the now-always-null `period_end`.
- **`apps/api/core/usage.py`** — `current_month_start()` and the
  `max(month start, plan_started_at)` floor. `get_monthly_usage` /
  `MonthlyUsage` were renamed to `get_credit_usage` / `CreditUsage`, and
  usage is counted from `Org.plan_started_at` alone. An org with no
  recharge on record gets no lower bound at all (all-time usage counts)
  rather than a fallback timestamp.
- **`apps/web/src/components/billing/usage-warning-banner.tsx`** — the
  "Renewal reminder — your plan renews in N days" case and its
  `RENEWAL_REMINDER_WINDOW_MS`. There's no upcoming deadline to warn about
  any more, so the banner is now purely credit-level based (low / used up).
  Its `past_due` case also went, because that state is now handled by the
  hard gate described below.
- **`apps/web/src/app/(dashboard)/billing/page.tsx`** — the "Access until
  {date}" / "Access ended {date}" line, and the `needsRenewal` flag derived
  from `billing_status`.

### Renamed by migration `e7a1c9b2d4f3` (not removed — values preserved)

| before | after |
| --- | --- |
| `plans.price_cents_monthly` (column) | `plans.price_cents` |
| `max_call_minutes_per_month` | `max_call_minutes` |
| `max_whatsapp_messages_per_month` | `max_whatsapp_messages` |
| `max_tokens_per_month` | `max_tokens` |
| `max_leads_per_month` | `max_leads` |

The last four are keys inside each plan's `limits` JSON. `max_seats`,
`max_campaigns` and `automated_followups` were never monthly and are
untouched. Applied to the shared Neon DB on 2026-08-16; all 7 plan rows
migrated, and a plan with empty `limits` was correctly skipped.

### What was added in its place

- **`apps/web/src/components/billing/credit-expired-modal.tsx`** — a
  centred recharge-prompt modal mounted once in `DashboardShell`. Shown when
  any metered credit is spent or billing lapsed; exempt on `/billing*` so it
  can't cover the checkout buttons that clear it. Dismissible (X, Escape,
  backdrop, "Remind me later"), but every dismissal only snoozes it for
  `SNOOZE_MS` (5 minutes) — it reopens on its own until the org recharges,
  rather than being closable for good.
- **`useIsOutOfCredit()`** in `apps/web/src/lib/hooks/useBilling.ts` — the
  single source of truth for "blocked", shared by the modal, the banner and
  both billing pages so they can't disagree. `isAnyCreditExhausted` is the
  underlying rule: *any one* metered credit hitting its limit blocks the
  org, not all of them — a plan with call minutes still left but WhatsApp
  spent is still blocked, since WhatsApp itself has actually stopped.
  Metrics with a `null` limit (unlimited) or a limit of `0` (channel not
  included in this plan at all — e.g. a WhatsApp-only plan's
  `max_call_minutes: 0`) are excluded, since neither can ever "run out" and
  counting a zero-credit channel would gate those orgs permanently with no
  recharge able to clear it.
- **`CREDIT_LIMIT_EVENT`** in `apps/web/src/lib/api.ts` — `apiFetch` fires a
  `window` event on any 402 so the modal appears immediately instead of
  waiting up to `POLL.billing` ms.

### What still exists (untouched)

- `BillingPayment.period_end` — the **column stays**, it's just written as
  `NULL` now. Existing rows keep whatever end date they were given before
  2026-08-16, so no history was destroyed.
- `Org.billing_status` and `deps.py`'s `_BLOCKED_BILLING_STATUSES` —
  `past_due` / `canceled` / `incomplete` still block every metric. Nothing
  *sets* them on a timer any more, so they're now only reached by a failed
  payment webhook or a deliberate admin action.
- `Org.plan_started_at` — unchanged column, but now load-bearing: it *is*
  the credit balance's start line, so moving it forward is what recharges
  an org.
- Razorpay one-time Orders, `/billing/checkout-session`,
  `/billing/verify-payment`, `/billing/webhook` — all unchanged. Checkout
  was already a one-time payment; only what the payment *grants* changed.

### How to re-add monthly periods

Not a re-import — expect a migration plus code on both sides:

1. `uv run alembic downgrade 22124d6b9b9d` to put the column and JSON keys
   back, then revert the corresponding names in `apps/api/db/models/plan.py`,
   `apps/api/schemas/billing.py`, `apps/api/routers/billing.py`,
   `apps/api/routers/admin.py`, the two workers, `realtime_bridge.py`, and
   the frontend hooks/components. **Do this in the same deploy as the
   downgrade**, not before or after it.
2. Restore `apps/api/workers/billing_expiry.py` from git history (it was
   deleted on 2026-08-16) and re-register it in `main.py`'s `lifespan`.
3. In `apps/api/routers/billing.py`, restore `_PERIOD_DAYS` and have
   `_activate_paid_payment` set `payment.period_end = now + timedelta(...)`
   again — the expiry worker keys entirely off that field, so it stays inert
   until this is done.
4. In `apps/api/core/usage.py`, reinstate the calendar-month floor
   (`max(current_month_start(), plan_started_at)`).
5. Decide what the credit gate should do — if periods come back, the
   `past_due` case belongs in the banner again and the gate should probably
   narrow to credit exhaustion only.

Tests to update: `apps/api/tests/test_usage_limits.py` has
`test_credit_usage_counts_only_since_last_recharge` and
`test_credit_usage_does_not_reset_on_calendar_month`, which assert exactly
the behaviour this section removed.

---

## Notes for whoever re-adds these
- Help Desk and Social Links share **one** DB row/table (`PlatformSettings`,
  id=1) and **one** PATCH endpoint (`/billing/platform-settings`), so
  re-adding one does not require touching the other. SMS is unrelated to
  that table entirely.
- Nothing was deleted from `apps/web/src/lib/hooks/index.ts`,
  `apps/web/src/lib/query.ts`, or `apps/web/src/lib/types.ts` — all exports
  used by the components above are still there.
- `git log` around 2026-08-14 has the exact diff if you want the original
  `SocialLinksRow` / tab JSX / SMS status-line JSX verbatim instead of
  rewriting it from this doc. Section 4's removals are in the 2026-08-16
  commits — that's also where the deleted `billing_expiry.py` lives.
- Sections 1–3 are independent of each other and of section 4. Section 4 is
  the only one where the frontend, the backend and the DB schema have to
  move together; treat it as one change, not three.
