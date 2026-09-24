# Split Campaigns & Follow-ups by channel + per-item org feature toggles

## Context
Today "Campaigns" is one sidebar item/page (`apps/web/src/app/(dashboard)/automation/campaigns/page.tsx` → `campaigns-view.tsx`) where voice vs WhatsApp is only a row-level filter, not a real separation — channel is decided per-contact-row in the uploaded file, not at creation time. "Automated Follow-up" is similarly one page (`automation/follow-ups/page.tsx`) with an in-dialog `channel` dropdown (`new-follow-up-rule-dialog.tsx`). Org-level feature gating currently exists only at a coarse group level (`AVAILABLE_ORG_FEATURES` in `apps/api/db/models/org.py`: `crm, appointments, follow_ups, sales, tickets, templates, conversations, human_support, follow_up_tasks` — no separate voice/whatsapp flags, no per-sidebar-item granularity).

The user wants: (1) Voice Campaigns and WhatsApp Campaigns as two distinct sidebar entries with their own pages, instead of one page with a dropdown/filter; (2) the same split for Automated Follow-ups; (3) organization create/edit to let an admin enable/disable **each individual sidebar item**, not just today's coarse groups. This gives orgs a cleaner UX (no channel-mixing) and finer admin control over what each org can see.

Confirmed approach: keep the backend data model unified (`CallCampaign`/`CampaignTarget`, `FollowUpRule`) and only split at the frontend routing/UI layer, filtering/creating by channel. Feature flags become per-sidebar-item instead of per-group.

## 1. Backend: new granular feature keys
File: `apps/api/db/models/org.py`
- Replace/extend `AVAILABLE_ORG_FEATURES` — split `"follow_ups"` into `"follow_ups_voice"` and `"follow_ups_whatsapp"`, and add `"campaigns_voice"` and `"campaigns_whatsapp"` (campaigns currently have no gating key at all — check `deps.py`/routers for confirmation and add gating if missing).
- Keep existing keys (`crm`, `appointments`, `sales`, `tickets`, `templates`, `conversations`, `human_support`, `follow_up_tasks`) as-is; only the ones being split change.
- `validate_org_features()` (same file) automatically validates against the updated tuple — no logic change needed, just the list.
- Migration: write an Alembic migration that, for existing orgs with `follow_ups` in `enabled_features`, rewrites it to `["follow_ups_voice", "follow_ups_whatsapp"]` (preserve current behavior — org that had follow-ups on keeps both channels on). No migration needed for campaigns since they aren't currently gated.

## 2. Backend: gate campaign endpoints (new)
File: `apps/api/deps.py` — reuse existing `require_feature(feature)` dependency (~line 205-220).
- Find the campaign routes in `apps/api/routers/admin.py` (e.g. `_create_campaign_from_rows`, `GET/POST` campaign endpoints) and add `Depends(require_feature("campaigns_voice"))` / `"campaigns_whatsapp"` as appropriate — likely gate at creation time based on the channel(s) present in the submitted rows, and gate list/read endpoints by allowing them if *either* flag is enabled (since a shared list may show both channels) — actual filtering of which rows/cards are visible happens at the UI/query layer once split (see §4).
- Update `apps/api/routers/follow_ups.py` similarly: swap the single `"follow_ups"` gate for `"follow_ups_voice"` / `"follow_ups_whatsapp"` depending on `rule.channel` on create, and allow list/read if either is enabled.

## 3. Frontend: feature flag mirror
File: `apps/web/src/lib/orgFeatures.ts`
- Update `ORG_FEATURES` list to match the new backend keys (split `follow_ups` entry into two; add `campaigns_voice`/`campaigns_whatsapp`).
- Update `FEATURE_GATED_ROUTES`: change the single `{ prefix: "/automation/follow-ups", feature: "follow_ups" }` entry into two entries pointing at the new split routes (see §4), and add two new entries for the new campaign routes.

## 4. Frontend: split routes & pages
Create four new route folders mirroring the existing `automation/campaigns` and `automation/follow-ups` structure:
- `apps/web/src/app/(dashboard)/automation/campaigns/voice/page.tsx`
- `apps/web/src/app/(dashboard)/automation/campaigns/whatsapp/page.tsx`
- `apps/web/src/app/(dashboard)/automation/follow-ups/voice/page.tsx`
- `apps/web/src/app/(dashboard)/automation/follow-ups/whatsapp/page.tsx`

Component work:
- `campaigns-view.tsx`: add a required `channel: "voice" | "whatsapp"` prop. Use it to (a) default/lock the `channelFilter` to that channel instead of showing "All channels", (b) hide the now-irrelevant channel selector, (c) show only the relevant script/number fields (voice script select vs WhatsApp number+template select) instead of both being conditionally rendered, (d) filter the campaign list query/results to that channel. The two new pages render `<CampaignsView channel="voice" />` / `<CampaignsView channel="whatsapp" />`.
- `new-follow-up-rule-dialog.tsx`: add a `channel` prop (or `defaultChannel` + lock it, removing the in-dialog dropdown when invoked from a channel-specific page) so each new page's "create rule" button opens the dialog pre-set and locked to that channel.
- `automation/follow-ups/page.tsx` logic (list, `ChannelBadge`, etc.) gets duplicated/parameterized into the two new voice/whatsapp pages, each querying/filtering rules by channel.
- Decide whether the old unified `automation/campaigns` and `automation/follow-ups` routes redirect to one of the new routes or are removed — recommend redirecting `automation/campaigns` → `automation/campaigns/voice` and `automation/follow-ups` → `automation/follow-ups/voice` for old bookmarks/links, using Next.js `redirect()`.

## 5. Frontend: sidebar
File: `apps/web/src/components/nav.tsx`
- In the `Automation` group of `GROUPS` (~line 60-105), replace the single `"Campaigns"` and `"Automated Follow-up"` entries with four entries: "Voice Campaigns" (`/automation/campaigns/voice`), "WhatsApp Campaigns" (`/automation/campaigns/whatsapp`), "Voice Follow-ups" (`/automation/follow-ups/voice`), "WhatsApp Follow-ups" (`/automation/follow-ups/whatsapp`), each tagged with its matching `feature` key for the existing `isFeatureDisabled()` filter (~line 165) — no new gating mechanism needed, just more entries using the mechanism that already exists.
- File: `apps/web/src/app/(dashboard)/layout.tsx` — `featureForRoute()` needs the new route prefixes added so direct URL visits to a disabled-feature route still bounce to `/`, matching current behavior for `/automation/follow-ups`.

## 6. Frontend: org create/edit feature checklist
File: `apps/web/src/components/organizations/org-feature-checklist.tsx`
- Since `ORG_FEATURES` (§3) now lists items 1:1 with sidebar entries, this checklist automatically becomes per-sidebar-item granular — verify labels read naturally (e.g. "Voice Campaigns", "WhatsApp Campaigns", "Voice Follow-ups", "WhatsApp Follow-ups") since `new-org-dialog.tsx` / `edit-org-dialog.tsx` render this list directly with no other change needed.

## Verification
1. Backend: run/update relevant pytest coverage for `require_feature` gating in `apps/api/routers/follow_ups.py` and campaign creation in `admin.py` (check `apps/api/tests/` for existing patterns to extend, e.g. tests around `follow_ups` gating).
2. Run the Alembic migration against a local/dev DB and confirm an org previously having `follow_ups` enabled now has both `follow_ups_voice` and `follow_ups_whatsapp`.
3. Frontend: `npm run dev` (or equivalent) in `apps/web`, then manually:
   - Confirm sidebar shows 4 separate Automation items instead of 2.
   - Create a Voice Campaign from `/automation/campaigns/voice` — confirm only voice fields shown, WhatsApp campaigns list unaffected.
   - Create a WhatsApp Campaign from `/automation/campaigns/whatsapp` — confirm only WhatsApp fields shown.
   - Create rules from both new Follow-up pages — confirm channel is locked/pre-set correctly and rules show up filtered on their respective page.
   - In Organizations → create/edit an org, toggle off e.g. "WhatsApp Campaigns" only, confirm that sidebar item disappears for a user in that org while "Voice Campaigns" remains, and direct navigation to `/automation/campaigns/whatsapp` bounces to `/`.
4. `npm run typecheck`/`lint` (or project's equivalent) and `pytest` in `apps/api` before considering done.
