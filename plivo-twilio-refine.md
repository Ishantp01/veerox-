# Plivo/Twilio calling: dual-provider numbers + manual selection

Date added: 2026-09-01

Previously an org could only ever have ONE dedicated calling number on
file — a single field (`plivo_phone_number`, misleadingly named) accepted
either a Plivo or a Twilio number, auto-detected which account owned it
(`channels/voice/number_provider.py::detect_provider`), and always cleared
the other provider's column when set. Which provider a call actually went
out on was decided automatically by `channels/voice/failover.py`, based
purely on which of `plivo_from_number`/`twilio_from_number` was non-null —
the org/user had no way to choose.

This refines both halves:

1. **An org can now have a dedicated number on BOTH providers at once.**
   `Org.plivo_phone_number` and `Org.twilio_phone_number` are set
   independently — org creation and the org-numbers settings endpoint each
   gained a second, explicit field instead of one auto-detected field.
2. **The AI Calling page lets the org manually choose which number to call
   from** — but ONLY when the org actually has both configured (nothing to
   choose between otherwise, so no toggle is shown and the single
   configured/default number is just used, unchanged from before).
3. The manual choice is a *preference*, not a hard restriction —
   `failover.initiate_call` still tries the other provider as a fallback on
   failure, same resilience as before.

Nothing about a single-provider org (the common case today) changes in
behavior — this only adds capability for orgs with both numbers.

## Files touched

Backend:
- `apps/api/schemas/admin.py` — `OrgNumbersIn`/`OrgNumbersOut` gained an
  explicit `twilio_phone_number` field (previously only `plivo_phone_number`,
  auto-detected). `OutboundCallIn` gained an optional `provider: Literal["plivo","twilio"] | None`.
- `apps/api/routers/admin.py` — `update_org_numbers` now sets
  `plivo_phone_number`/`twilio_phone_number` independently instead of
  auto-detecting via `resolve_calling_number` and clearing the other column.
  `outbound_call` passes `payload.provider` through to `initiate_call` as
  `preferred_provider`.
- `apps/api/schemas/auth.py` — `ProvisionOrgIn` gained an explicit
  `twilio_phone_number` field alongside `plivo_phone_number`.
- `apps/api/routers/auth.py` — `provision_org` sets both `Org` columns
  directly from the two explicit fields (digits-only), no longer runs
  `resolve_calling_number`/`detect_provider` at all.
- `apps/api/channels/voice/failover.py` — `initiate_call` gained a
  `preferred_provider: str | None` param that overrides the automatic
  from-number-based ordering when set; falls back to the other provider on
  failure exactly as before either way.

Frontend:
- `apps/web/src/components/organizations/new-org-dialog.tsx` — the single
  "Dedicated calling number" field is now two fields: "Dedicated Plivo
  number" and "Dedicated Twilio number".
- `apps/web/src/app/(dashboard)/calling/page.tsx` — new "Call from"
  dropdown (Plivo/Twilio), rendered only when `useOrgNumbers()` reports both
  `plivo_phone_number` and `twilio_phone_number` set. The "via Plivo" /
  "Plivo calls the recipient…" copy is now provider-aware.
- `apps/web/src/lib/hooks/useOutbound.ts` — `OutboundCallInput` gained an
  optional `provider` field, sent through to `POST /admin/outbound/call`.
- `apps/web/src/lib/hooks/useAdminOrgs.ts` — `ProvisionOrgInput` gained
  `twilio_phone_number`.

Tests added:
- `apps/api/tests/test_voice_failover.py` — `preferred_provider` ordering
  and its fall-through-on-failure behavior.
- `apps/api/tests/test_admin_endpoints.py` —
  `test_update_org_numbers_sets_both_providers_independently`,
  `test_outbound_call_passes_chosen_provider_through`.

## Not in scope (deliberately)

- **Campaign calls** (`apps/api/workers/campaign_dialer.py`) still resolve
  provider automatically from `Org.plivo_phone_number`/`twilio_phone_number`
  with no manual choice — `CallCampaign` has no provider-preference column.
  The ask was specifically about the AI Calling page's single-call dial
  flow; campaigns would need a new `CallCampaign` column if this is wanted
  there too.
- Numbers entered in either the new-org dialog or the org-numbers settings
  fields are no longer verified against the claimed provider's account
  (previously `detect_provider` at least confirmed *some* account owned the
  number, even though it also silently reassigned it to whichever one did).
  Trusting the explicit field now — a typo'd/wrong-provider number will
  simply fail to place a call rather than being auto-corrected.
