# Super admin: per-org feature checklist + team member limit

## Context
Right now the platform admin (`is_superuser`, `PlatformAdminDep`/`verify_platform_admin` in
[deps.py](apps/api/deps.py#L417)) can only manage an org's name, numbers, credentials, and
license status ([billing.py](apps/api/routers/billing.py)). There is no way to:
1. Restrict which product modules (CRM/leads, appointments, follow-ups, calling campaigns,
   helpdesk widget, support tickets, WhatsApp templates, conversations inbox) a given
   organization can use.
2. Cap how many team members an org can invite.

The old Razorpay-based Plan/billing_status system that used to carry this kind of
per-org restriction was fully removed (`docs/razorpay-billing-removal.md`) in favor of the
simpler manual `license_status`. This adds the two specific controls requested — a feature
checklist and a member seat cap — as plain admin-managed `Org` fields, following the exact
same "manually admin-set field, enforced by a `deps.py` dependency" pattern already used for
`license_status`. No new "plan" concept, no billing.

## Feature keys
A fixed, hardcoded list (not user-editable) mapping to existing routers:
`crm`, `appointments`, `follow_ups`, `sales` (voice/WhatsApp calling campaigns), `helpdesk`,
`tickets`, `templates`, `conversations`. Team management, billing/license, and core admin
settings are never gated — every org keeps those regardless of the checklist.

## Backend changes

**1. `apps/api/db/models/org.py`** — add two columns to `Org`:
- `enabled_features: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)`
  — empty list/omitted historically = *no explicit restriction recorded*; treat `[]` (or a
  brand-new org, see below) as "all features enabled" so existing orgs aren't silently
  locked out on migration day. Use `list[str] | None` with `None` = unrestricted, `[]` =
  everything off, to make "not yet configured" distinguishable from "admin explicitly
  disabled everything" — mirrors the `None`-means-unlimited convention on the member cap.
- `max_team_members: Mapped[int | None] = mapped_column(Integer, nullable=True)` — `None` =
  unlimited (today's behavior, unchanged for existing orgs).

Follow the existing comment style (explain the *why*, reference the enforcement site), same
as the `license_status` block just above.

**2. Alembic migration** — new revision under `migrations/versions/`, following the most
recent ones (e.g. `f010beb97c28_add_org_openai_api_key_encrypted.py`) for structure: add
both columns nullable, no backfill needed since `None` is the correct default for both.

**3. `apps/api/deps.py`** — add a `require_feature(key)` dependency factory right after
`enforce_org_license`, mirroring it exactly:
- `async def is_org_feature_enabled(db, org_id, feature) -> bool`: platform-admin-owned org
  (`_org_is_platform_admin_owned`) is always exempt (same as license). Otherwise look up
  `Org.enabled_features`; `None` → `True` (unrestricted); else `feature in enabled_features`.
- `def require_feature(feature: str)` returns an async dependency taking `RequestOrgDep` +
  `DbDep`, raising `HTTPException(403, detail={"error": "feature_disabled", "feature": ...})`
  if `not await is_org_feature_enabled(...)`.
- Wire it into the relevant routers' `APIRouter(..., dependencies=[...])` lists alongside
  the existing `verify_admin_or_session` dep: `crm.py`, `appointments.py`, `follow_ups.py`,
  `sales.py`, `helpdesk.py`, `tickets.py`, `templates.py`, `conversations.py` each get
  `Depends(require_feature("<own key>"))` appended.

**4. Member limit — `apps/api/routers/team.py`'s `invite_member`** (currently
[team.py:138](apps/api/routers/team.py#L138), no limit check today): before creating the
new `OrgMembership`, if `org.max_team_members is not None`, count existing
`OrgMembership` rows for `org.org_id` (same query pattern already used in
[billing.py:164](apps/api/routers/billing.py#L164) for `seat_count`) and raise
`HTTPException(409, "Team member limit reached for this organization")` if the count is
already `>= max_team_members`. Platform-admin-owned org: no need to special-case — it simply
won't have a `max_team_members` set.

**5. Schemas / billing router:**
- `apps/api/schemas/billing.py`: add `enabled_features: list[str] | None = None` and
  `max_team_members: int | None = None` to both `OrgAdminOut` and `OrgUpdateIn`.
- `apps/api/schemas/auth.py`: add the same two optional fields to `ProvisionOrgIn` (so a
  platform admin can set them at creation time) — `ProvisionOrgOut` doesn't need them.
- `apps/api/routers/billing.py`: `_org_admin_out` (line 106) passes through the new fields;
  `update_org` (line 181) already does a generic `setattr` loop over `fields.items()` for
  plain fields, so `enabled_features`/`max_team_members` need no special-casing there —
  they'll fall through the same `for field, value in fields.items(): setattr(...)` loop
  used for `name` today, as long as they're not popped out beforehand.
  `routers/auth.py`'s `provision_org` (line 122): set `org.enabled_features =
  payload.enabled_features` and `org.max_team_members = payload.max_team_members` when
  constructing `Org(...)`.
- Expose the fixed feature-key list via a small constant (e.g.
  `AVAILABLE_ORG_FEATURES = ("crm", "appointments", "follow_ups", "sales", "helpdesk",
  "tickets", "templates", "conversations")` in `org.py`) importable by both the backend
  validation (reject unknown keys in `OrgUpdateIn`/`ProvisionOrgIn` — simple validator) and
  reused by the frontend checklist labels.

## Frontend changes

**1. `apps/web/src/lib/hooks/useAdminOrgs.ts`** — add `enabled_features: string[] | null` and
`max_team_members: number | null` to `AdminOrg`, `UpdateOrgInput`, and `ProvisionOrgInput`.

**2. `apps/web/src/components/organizations/edit-org-dialog.tsx`** — add a new form section
(same collapsible-card style as "Provider setup") with:
- A checklist (checkbox per feature key from a shared constant, e.g. CRM, Appointments,
  Follow-ups, Calling & Campaigns, Helpdesk widget, Support tickets, WhatsApp templates,
  Conversations inbox) — `null`/unset state on load renders all checked (matches backend's
  "unrestricted" default).
- A "Max team members" number input, blank = unlimited (mirrors how license fields already
  handle optional numeric values elsewhere in this codebase).
Wire both into `formFromOrg`/the submit payload the same way `orgName` etc. are handled.

**3. `apps/web/src/components/organizations/new-org-dialog.tsx`** — add the same "Max team
members" input (optional) and feature checklist at creation time, defaulting to all-checked,
submitted via `ProvisionOrgInput`.

## Verification
- `alembic upgrade head` locally (or against the dev DB per
  [veerox-db-bootstrap](../../memory note) conventions) to confirm the migration applies
  cleanly with no backfill errors.
- Backend: add/extend a pytest similar to `apps/api/tests/test_org_license.py` — one for
  `require_feature` (disabled feature → 403 on e.g. `/crm/...`, platform-admin org exempt),
  one for the `invite_member` seat-limit 409.
- Manual: as platform admin, edit an org to disable "CRM" and set max members to 1 in
  the Organizations page UI; confirm that org's dashboard user gets a 403 hitting `/crm`
  endpoints, and that inviting a second team member for that org returns the 409 with a
  clear toast message. Re-enable and confirm access returns.
