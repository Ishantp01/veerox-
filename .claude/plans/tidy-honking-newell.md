# Multi WhatsApp number + multi WhatsApp script support

## Context

Voice/Plivo calling already supports multiple dedicated phone numbers per org
(`OrgPhoneNumber`, provider `"plivo"`/`"twilio"`) and a full script library
per org (`Script`), with per-campaign overrides (`CallCampaign.phone_number_id`,
`CallCampaign.script_id`) falling back to an org-wide default. WhatsApp never
got the same treatment: an org has exactly one WhatsApp number
(`Org.whatsapp_phone_number_id`, a single column) and exactly one script
(`Org.script`, a single text field), with no per-campaign choice.

The user wants WhatsApp brought to parity: multiple WhatsApp numbers per org
(like Plivo numbers today) and multiple WhatsApp scripts, selectable per
campaign the same way voice campaigns already pick a script/number. Per the
user's answers: numbers stay platform-admin-managed (same dialog as
Plivo/Twilio, not self-serve), all WhatsApp numbers keep sharing the single
global Meta app/WABA/access token (`config.py`'s `meta_*` settings) —
`phone_number_id` is just which number a message is attributed to and sent
from, not a different Meta app.

This is a mechanical generalization of an existing, working pattern, not new
architecture — every step below has a direct voice-side analogue to copy.

## Backend

### 1. Data model

- **`apps/api/db/models/org_phone_number.py`**: extend `provider` to also
  accept `"whatsapp"`. The `phone_number` column stores Meta's
  `phone_number_id` string for WhatsApp rows (same free-text column, just a
  different provider bucket) — no new table needed, `OrgPhoneNumber` already
  supports several rows per `(org_id, provider)` with `is_default`/`position`.
- **`apps/api/db/models/script.py`**: add a `channel` column
  (`String(10)`, not null, `server_default="voice"`) so `Script` covers both
  channels instead of being voice-only. Existing rows backfill to `"voice"`.
- **`apps/api/db/models/call_campaign.py`**: add `whatsapp_script_id`
  (FK `scripts.id`, `ondelete="SET NULL"`, nullable) and
  `whatsapp_number_id` (FK `org_phone_numbers.id`, `ondelete="SET NULL"`,
  nullable) — siblings of the existing voice-only `script_id`/
  `phone_number_id`, same nullable/SET NULL contract and doc-comment style.
- **`apps/api/db/models/org.py`**: drop `whatsapp_phone_number_id` (fully
  replaced by `OrgPhoneNumber` rows, mirroring how that table already
  replaced the old single-column `plivo_phone_number`/`twilio_phone_number`).
  Keep `Org.script` as-is — it stays the final text fallback when an org has
  no WhatsApp `Script` rows at all (cheap backward compat, zero migration
  risk for existing orgs).

### 2. Migration (new Alembic revision)

- Add `scripts.channel` (`server_default='voice'`, not null).
- Add `call_campaigns.whatsapp_script_id`, `call_campaigns.whatsapp_number_id`.
- Data-migrate: for every `orgs` row with `whatsapp_phone_number_id` set,
  insert one `org_phone_numbers` row
  (`provider='whatsapp'`, `phone_number=<that value>`, `is_default=true`,
  `position=0`).
- Drop `orgs.whatsapp_phone_number_id`.
- Update the `provider` `Literal`/check in
  **`apps/api/schemas/org_numbers.py`** (`OrgPhoneNumberIn`/`Out`) to
  `Literal["plivo", "twilio", "whatsapp"]`.

### 3. Resolution logic (the actual behavior change)

- **`apps/api/channels/whatsapp/adapter.py::_resolve_org_id`**: replace the
  `Org.whatsapp_phone_number_id == phone_number_id` lookup with a query
  against `OrgPhoneNumber` (`provider="whatsapp"`, `phone_number ==
  phone_number_id`) joined to get `org_id`. Same fallback to
  `settings.default_org_id` on miss.
- **`apps/api/core/agent.py`**: `handle_turn` already loads the
  `CampaignTarget` row for `campaign_target_id` around line 313 before
  calling `_system_prompt_for` — reuse that fetch (don't re-query) to also
  pull `CallCampaign.whatsapp_script_id` when the channel is `"whatsapp"`,
  and pass the resolved `Script` content (or `None`) into
  `_system_prompt_for`. New resolution order for the WhatsApp base prompt:
  campaign's pinned `whatsapp_script_id` → org's default `Script` row
  (`channel="whatsapp"`, `is_default=True`) → `Org.script` (legacy text
  fallback) → `OUTBOUND_CALL_PROMPT`. This mirrors
  `channels/voice/realtime_bridge.py::_system_instructions`'s existing
  resolution order for voice — copy that logic's shape, not its code (it's
  sync/different session lifecycle).
- **`apps/api/workers/whatsapp_dispatcher.py::_claim_targets`**: the query
  currently joins `Org.whatsapp_phone_number_id` directly. Change it to
  `LEFT JOIN OrgPhoneNumber` twice conceptually — actually resolve per
  claimed row: prefer `CallCampaign.whatsapp_number_id`'s
  `OrgPhoneNumber.phone_number`, else the org's default WhatsApp number
  (`OrgPhoneNumber` where `provider="whatsapp"`, `is_default=True`), else
  `None` (platform default). Simplest implementation: join `CallCampaign` →
  `OrgPhoneNumber` (via `whatsapp_number_id`, outer join) for the pinned
  case, and separately preload each distinct `org_id`'s default WhatsApp
  number (small dict, same batching style already used there for
  `org_over_limit`) for the fallback case.
- **`apps/api/routers/admin.py`** manual single-message send route (around
  line 2911-2963): replace `org_record.whatsapp_phone_number_id` with a
  lookup of that org's default `OrgPhoneNumber` (`provider="whatsapp"`,
  `is_default=True`), falling back to `None`. No new UI to pick a specific
  number for one-off admin sends — out of scope, matches today's behavior of
  "the org's one number" now generalized to "the org's default number".

### 4. Routes

- **`apps/api/routers/admin.py`** `GET`/`PUT /org-numbers`: drop the special
  `whatsapp_phone_number_id` field entirely — it's now just another
  `provider` value inside the existing `phone_numbers` list, so
  `replace_org_phone_numbers` (in `channels/voice/org_numbers.py`) handles it
  for free once the `Literal` accepts `"whatsapp"`. Update
  **`apps/api/schemas/admin.py`**'s `OrgNumbersOut`/`OrgNumbersIn` to drop
  the standalone field.
- Campaign create routes (JSON + multipart variants, ~line 1719 and ~1810)
  and the PATCH route (~line 2080): add `whatsapp_script_id`/
  `whatsapp_number_id` params, validated with the exact same
  ownership-check pattern already there for `script_id`/`phone_number_id`
  (`script.org_id != org_id` / `number.org_id != org_id` → 400).
- WhatsApp script CRUD: extend the existing `/admin/scripts` list/create
  endpoints (~line 2360+) to accept a `channel` query/body param
  (default `"voice"` for back-compat), filtering `Script` rows by it, rather
  than duplicating the whole CRUD block for a second `/admin/whatsapp-scripts`
  path. `PATCH/POST .../set-default/DELETE /admin/scripts/{id}` need no
  change — they already operate on a single `Script` row by id regardless of
  channel, just need the ownership check untouched.

## Frontend

- **`apps/web/src/components/organizations/edit-org-dialog.tsx`** and
  **`new-org-dialog.tsx`**: remove the single `whatsappNumberId` text
  `Input` (dialog.tsx line ~47/120/236-251); add a third
  `PhoneNumberListField` instance for `provider="whatsapp"`, same as the
  existing Plivo/Twilio ones (lines ~224-235).
- **`apps/web/src/lib/hooks/useOrgNumbers.ts`**: drop the standalone
  `whatsapp_phone_number_id` field from the org-numbers type/mutation now
  that it's folded into the `phone_numbers` list.
- **`apps/web/src/components/settings/settings-view.tsx`** +
  **`script-library.tsx`**: the `channel === "whatsapp"` branch currently
  renders `ScriptEditor` (single textarea). Swap it for `ScriptLibrary`
  parametrized by `channel="whatsapp"` (the component/hook
  (`useScripts.ts`) already exists for voice — thread a `channel` prop/param
  through instead of duplicating the component). Leave `ScriptEditor` itself
  in place unused-but-not-deleted only if something else still references
  it — otherwise delete it.
- **`apps/web/src/components/campaigns/campaigns-view.tsx`** (+ wherever the
  campaign create/edit form actually lives — grep the same file for where
  `script_id`/`phone_number_id` are picked for voice): add the equivalent
  `whatsapp_script_id`/`whatsapp_number_id` selectors, shown when the
  campaign's channel is `"whatsapp"` or `"mixed"`, mirroring the existing
  voice selector markup/hook usage 1:1.
- **`apps/web/src/lib/types.ts`** and **`useCampaigns.ts`**: add the two new
  optional campaign fields alongside the existing `script_id`/
  `phone_number_id` typings.

## Verification

- Run the existing test suite (`apps/api`'s pytest) after the migration —
  particularly any tests touching `org_phone_numbers`, `scripts`,
  `whatsapp/adapter.py`, and `whatsapp_dispatcher.py`, since those are the
  files with real logic changes, not just additive columns.
- Manually run the Alembic migration against a local/dev DB and confirm: an
  org that had `whatsapp_phone_number_id` set now has exactly one
  `org_phone_numbers` row with `provider='whatsapp', is_default=true` and
  the same value.
- In the platform-admin org dialog, add a second WhatsApp number to a test
  org, save, and confirm both rows come back from `GET /admin/org-numbers`.
- Create a WhatsApp (or mixed) campaign, pick a non-default WhatsApp script
  and number for it, and send a test inbound message on that number's
  `phone_number_id` (simulate the Meta webhook payload) — confirm it
  resolves to the right org and the reply uses the campaign's pinned script
  and goes out from the pinned number, not the org default.
- Confirm an org/campaign with no WhatsApp number or script set at all still
  behaves exactly as before (platform default number, `Org.script` or
  `OUTBOUND_CALL_PROMPT`).
