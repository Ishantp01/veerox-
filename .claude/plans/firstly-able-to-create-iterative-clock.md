# Multiple AI-calling scripts + per-campaign number/script selection

## Context

Today AI voice calling uses a single org-wide script: `Org.script` (`apps/api/db/models/org.py:57`), a lone `Text` column shared between voice and WhatsApp. Campaign creation has no way to pick which script or which phone number to use — every campaign call falls back to that one shared script, and the outbound number is decided purely by round-robin rotation across all of an org's numbers (`apps/api/channels/voice/org_numbers.py::get_rotating_numbers`).

The user wants to run different calling scripts for different campaigns (e.g. one script per offer/audience) and to optionally pin a campaign to a specific caller-ID number, selected at campaign-creation time via dropdowns — with a script library manageable from Settings > AI Calling, including a default script used whenever a campaign doesn't specify one.

Decisions confirmed with the user:
- **Voice-only.** WhatsApp keeps using `Org.script` / the existing `ScriptEditor` untouched — nothing in the WhatsApp code path changes.
- **Phone number dropdown is optional**; leaving it blank keeps today's rotation behavior.
- **Settings manages the full script library** (create/edit/delete/set-default), not just a default picker.

Migration is additive only: `Org.script` is never modified or dropped (WhatsApp still needs it). A new `scripts` table is seeded from each org's existing `Org.script` value as a "Default" entry, purely as a starting point for the new voice library.

## Backend changes

**New model** — `apps/api/db/models/script.py` (new file, styled after `org_phone_number.py`):
`Script(id, org_id FK→orgs CASCADE, name, content: Text, is_default: bool, created_at, updated_at)`. Exactly one `is_default=True` per org, enforced in application code (no partial unique index — same reasoning as `OrgPhoneNumber.is_default`). Register in `apps/api/db/models/__init__.py`. Add `Org.scripts` relationship in `org.py` (after the `phone_numbers` relationship, line 73-75); leave `Org.script` (line 57) as-is with an updated comment noting it's WhatsApp-only now.

**`apps/api/db/models/call_campaign.py`**: add two nullable FK columns after `custom_message` (line 51), before `created_at`:
```python
script_id: Mapped[UUID | None] = mapped_column(ForeignKey("scripts.id", ondelete="SET NULL"), nullable=True)
phone_number_id: Mapped[UUID | None] = mapped_column(ForeignKey("org_phone_numbers.id", ondelete="SET NULL"), nullable=True)
```
`SET NULL` on delete so removing a script/number never blocks the delete or destroys the campaign — it just falls back to org-default/rotation.

**New schemas** — `apps/api/schemas/script.py` (new file, separate from `schemas/admin.py`'s existing singleton `ScriptIn`/`ScriptOut` which stay untouched for WhatsApp): `ScriptOut`, `ScriptCreateIn` (name, content, is_default), `ScriptUpdateIn` (name/content only — default-setting is its own endpoint). `schemas/campaign.py`'s `CampaignOut`: add `script_id: UUID | None`, `phone_number_id: UUID | None`.

**New endpoints** in `apps/api/routers/admin.py`, added right after the existing `/admin/script` handlers (~line 1986), using the same `RequestOrgDep` + `db.get(Org, org)` → 404 pattern as the rest of the file:
- `GET /admin/scripts` — list org's scripts.
- `POST /admin/scripts` — create; if it's the org's first script, force `is_default=True` regardless of input (mirrors `OrgPhoneNumber`'s "first entry becomes default" fallback); otherwise honor `is_default` and unset any prior default in the same transaction.
- `PATCH /admin/scripts/{id}` — rename/edit content (`exclude_unset`).
- `POST /admin/scripts/{id}/set-default` — atomically unset old default, set new.
- `DELETE /admin/scripts/{id}` — no auto-promotion of another script as default.

**Campaign creation** (`create_campaign`, `admin.py:1607`): accept new optional `Form` params `script_id`, `phone_number_id`; validate each belongs to the requesting org (404/400 otherwise) before threading them through `_create_campaigns_from_rows` (line 1526) → `_create_campaign_from_rows` (line 1374) into the `CallCampaign(...)` constructor. Update `_campaign_out` (line 1356) to include both fields in the response. The other two callers of `_create_campaigns_from_rows` (`/admin/leads/import`, `/admin/leads/bulk`) need no change — new kwargs default to `None`.

**Migrations** (`migrations/versions/`, chained off current head `a3b4c5d6e7f8`), two files:
1. Create `scripts` table + index on `org_id`; backfill one `is_default=True` row per org named "Default" from any non-null `orgs.script`. Downgrade just drops the table — `orgs.script` was never touched.
2. Add `script_id`/`phone_number_id` nullable FK columns to `call_campaigns`. Downgrade drops both columns.

**Script resolution at call time** — `apps/api/channels/voice/realtime_bridge.py::_system_instructions` (lines 175-203): resolve in order — campaign's `script_id` (if set) → org's `is_default=True` script (if any) → platform default `OUTBOUND_CALL_PROMPT`. Merge the function's two existing DB session blocks into one; drop the now-unused `Org` import, add `Script`. Preserve existing behavior of appending `campaign_qualification_append(...)`, `current_datetime_block()`, `VOICE_APPEND` after the resolved base script.

**Campaign phone-number pin** — `apps/api/workers/campaign_dialer.py`, in `_claim_targets` (~line 162), the actual call-placing path (`run_campaign_dialer` → `_dial_batch` → `_claim_targets`/`_dial_one` → `voice_failover.initiate_call`): select `CallCampaign.phone_number_id` alongside the existing columns; after the org's rotating-number lookup, bulk-fetch any pinned `OrgPhoneNumber` rows for the ids present in this batch. When a target's campaign has a pinned number, use that number/provider directly (set `plivo_from`/`twilio_from` and override `preferred_provider` to that number's provider so it's actually dialed from, not just tried first) and skip the rotation counter entirely for that target, so pinning a number doesn't perturb rotation for the rest of the org's pool. `_dial_one` / `initiate_call` need no signature changes — the override happens before their tuples are built.

## Frontend changes

**New hook file** — `apps/web/src/lib/hooks/useScripts.ts` (plural; distinct from the existing singular `useScript.ts`, which is untouched and keeps serving WhatsApp): `useScripts()`, `useCreateScript()`, `useUpdateScriptLibraryItem()` (disambiguated name — `useScript.ts` already has a `useUpdateScript`), `useSetDefaultScript()`, `useDeleteScript()`, all invalidating a new `queryKeys.scripts()` key (`apps/web/src/lib/query.ts`). Export from the hooks barrel (`apps/web/src/lib/hooks/index.ts`).

**New type** — `apps/web/src/lib/types.ts`: `ScriptLibraryItem` (id, name, content, is_default, created_at, updated_at), named distinctly from the existing singular `Script` type used by WhatsApp. Add `script_id`/`phone_number_id` to the `Campaign` interface to match the updated API response.

**New component** — `apps/web/src/components/settings/script-library.tsx` (styled after the existing template-management pattern, e.g. `templates/page.tsx` + `new-template-dialog.tsx`): a table of scripts (name, content preview, default badge, Edit/Set-default/Delete actions with the app's existing `useConfirm()` for delete), a "New script" dialog (name + content textarea + optional "set as default"), using existing `Table`/`Dialog`/`EmptyState`/`QueryBoundary` primitives — no new UI primitives needed.

**`apps/web/src/components/settings/settings-view.tsx`**: the "Script" `CollapsibleSection` (lines 231-237) branches on channel — render the new `ScriptLibrary` for `channel === "calling"`, keep the existing `ScriptEditor` (lines 62-146, unmodified) for WhatsApp. Update the file's top docstring, which currently claims the script is shared by both channels.

**`apps/web/src/components/campaigns/campaigns-view.tsx`** (creation form, lines 267-459): add two optional `Select` dropdowns — phone number (from `useOrgNumbers()`, already returns everything needed: id/provider/phone_number/is_default) and script (from the new `useScripts()`), each with an empty "Automatic" / "Use org default" option. Since campaign channel is resolved per-row from the uploaded file rather than chosen up front (a single upload can mix voice and WhatsApp targets — confirmed via `_resolve_row_channels`), place both dropdowns unconditionally with helper text noting "Voice calls only — ignored for WhatsApp contacts in this upload." Wire both into the existing create-campaign state/reset logic and payload.

**`apps/web/src/lib/hooks/useCampaigns.ts`**: extend `CreateCampaignInput` with optional `scriptId`/`phoneNumberId`, append them to the outgoing `FormData` as `script_id`/`phone_number_id` when present.

## Verification

- Run the migration locally (`alembic upgrade head`) and confirm existing orgs with a non-null `orgs.script` each get exactly one seeded `scripts` row with `is_default=True`; confirm `orgs.script` itself is unchanged.
- Backend: exercise `/admin/scripts` CRUD (create first script → auto-default; create second → not auto-default; set-default; delete) and confirm `POST /admin/campaigns` accepts/validates `script_id`/`phone_number_id`, rejecting ids from another org.
- Place a test campaign call with a pinned script/number and confirm (via logs or a debug breakpoint in `_system_instructions`/`_claim_targets`) the pinned script's content and pinned number/provider are actually used instead of the org default/rotation; place a call for a campaign with neither set and confirm the org default script and normal rotation still apply, matching pre-change behavior.
- Frontend: start the dev server, open Settings > AI Calling and manage scripts (create, edit, set default, delete) via `webapp-testing`/Playwright or manual browser check; create a campaign, confirm both new dropdowns populate from real org data and an unselected value still creates a working campaign.
