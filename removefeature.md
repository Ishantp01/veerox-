# Removed features: Help Desk Chatbot, Social Media Links & SMS notification text

Date removed: 2026-08-14

All three were removed from user-visible UI only (Settings page tabs,
navbar/dashboard chrome, and — for SMS — a status line in a dialog). All
backend routes, DB models, and the underlying React components/hooks/side
effects were left intact and untouched so any of them can be re-enabled by
wiring the UI back up — no backend or data work needed.

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
  rewriting it from this doc.
