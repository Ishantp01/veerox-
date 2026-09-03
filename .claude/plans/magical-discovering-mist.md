# Multiple Plivo/Twilio numbers per org

## Context

Today an `Org` can have exactly **one** Plivo number and **one** Twilio number — enforced by two scalar, globally-`unique=True` columns on `orgs` (`plivo_phone_number`, `twilio_phone_number`, [org.py:65-84](apps/api/db/models/org.py#L65-L84)). The org-create dialog (platform admin → New Organization) and the org-edit dialog each expose exactly one text input per provider.

The user wants the option, when creating or updating an org, to attach **more than one number per provider** (e.g. an org might own three Plivo DIDs across departments/campaigns, all of which should ring in to the same AI backend). This requires a real schema change (scalar column → child table) because the current uniqueness/shape makes multiple numbers impossible, not just hard to enter.

Research confirmed there are **three backend write paths** that touch these fields today (`POST /auth/provision-org` create, `PATCH /billing/orgs/{id}` platform-admin edit — what the edit dialog actually calls, and `PUT /admin/org-numbers` org self-service — currently unused by any frontend UI) and **five call sites** that read an org's number to place an outbound call (`admin.py outbound_call`, `campaign_dialer.py`, `follow_up_dispatcher.py`, `core/tools.py`'s AI callback tool) plus one inbound resolver (`webhook.py _resolve_org_by_number`). All of these currently assume "one column = one number" and must move to querying a new table.

**Design decision (stated up front since it shapes everything below):** numbers are managed as a list per provider, with exactly one number per provider optionally flagged `is_default`. Inbound-call routing works off *any* number in the list (a bonus improvement — today only the single column can ever match). Outbound calling (admin single-call, campaigns, follow-ups, AI callback) continues to dial from each provider's **default** number, same as today's single-number behavior — building "choose which of N numbers to dial from" per outbound call is out of scope; the user only asked for multiple numbers to be *addable*, not for per-call selection UI. Default-per-provider uniqueness is enforced in application code (not a DB constraint), since a Postgres partial-unique-index isn't practical to also work correctly on the SQLite backend the test suite runs against.

## Backend

### 1. New model — `apps/api/db/models/org_phone_number.py`
```python
class OrgPhoneNumber(Base):
    __tablename__ = "org_phone_numbers"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)   # "plivo" | "twilio"
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)  # digits-only, same convention as today's columns
    is_default: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("provider", "phone_number", name="uq_org_phone_numbers_provider_number"),)
```
Add `phone_numbers: Mapped[list["OrgPhoneNumber"]] = relationship(cascade="all, delete-orphan")` on `Org` ([org.py](apps/api/db/models/org.py)); remove the `plivo_phone_number`/`twilio_phone_number` columns (keep `preferred_voice_provider` and `whatsapp_phone_number_id` as-is — out of scope).

### 2. Migration — `migrations/versions/<rev>_add_org_phone_numbers_table.py`
Follow the existing style seen in `c8d9e0f1a2b3_add_org_channel_numbers.py` / `a1b2c3d4e5f6_add_org_twilio_phone_number.py` (docstring header explaining why + cross-references, hex revision id, explicit `branch_labels=None`/`depends_on=None`).
- `upgrade()`: create `org_phone_numbers` table + unique constraint + `org_id` index; **backfill** by inserting one row per org per existing non-null `plivo_phone_number`/`twilio_phone_number` (each becomes `is_default=true`, since it's already "the" number for that provider) using `sa.table()`/`op.get_bind()` — this repo's past migrations never needed a data migration, so this is the first one; then drop `uq_orgs_plivo_phone_number`/`uq_orgs_twilio_phone_number` and the two columns from `orgs`.
- `downgrade()`: reverse — re-add columns + constraints, backfill from `org_phone_numbers` where `is_default=true`, drop the table.

### 3. Shared helper — `apps/api/channels/voice/org_numbers.py` (new)
```python
async def get_default_numbers(db, org_id) -> tuple[str | None, str | None]:
    """(plivo_from_e164, twilio_from_e164) — each provider's default number for
    this org, or None. Re-prefixes stored digits-only with '+' like the old
    org.plivo_phone_number/twilio_phone_number reads did."""

async def replace_org_phone_numbers(db, org_id, numbers: list[OrgPhoneNumberIn]) -> list[OrgPhoneNumber]:
    """Delete-and-reinsert this org's number set in one transaction (used by
    all three write paths below). Normalizes digits-only. If a provider has
    >1 entry marked is_default, keeps the first and clears the rest — mirrors
    this codebase's existing 'trust but sanitize the caller' style (see
    update_org_numbers' docstring). If a provider has entries but none
    marked default, the first one becomes it. Raises on IntegrityError the
    same way provision_org/update_org do today (409, number already owned by
    another org)."""
```
`get_default_numbers` replaces the direct `org.plivo_phone_number`/`org.twilio_phone_number` reads at:
- `apps/api/routers/admin.py` `outbound_call` (~lines 2248-2250)
- `apps/api/workers/follow_up_dispatcher.py` (~lines 280-293)
- `apps/api/core/tools.py` AI callback tool (~lines 1105-1118)

`apps/api/workers/campaign_dialer.py` `_claim_targets()` (~lines 152-159) is a single hot-path SQL query, not an ORM attribute read — it gets two `LEFT JOIN`s against `org_phone_numbers` (aliased per provider, filtered `is_default = true`) instead of selecting the two columns directly, preserving its single-query performance.

### 4. Inbound routing — `apps/api/channels/voice/webhook.py`
`_resolve_org_by_number` (lines 114-136) changes from `select(Org.id).where(Org.twilio_phone_number == normalized)` (or plivo) to `select(OrgPhoneNumber.org_id).where(OrgPhoneNumber.provider == provider, OrgPhoneNumber.phone_number == normalized)`. This is strictly more correct than today — any of an org's numbers on that provider now resolves inbound calls, not just "the" column value.

### 5. Schemas — add one shared pair, reuse everywhere
In `apps/api/schemas/admin.py` (or a shared `schemas/common.py` if one exists — check first):
```python
class OrgPhoneNumberIn(BaseModel):
    provider: Literal["plivo", "twilio"]
    phone_number: str
    is_default: bool = False

class OrgPhoneNumberOut(BaseModel):
    id: UUID
    provider: Literal["plivo", "twilio"]
    phone_number: str
    is_default: bool
    created_at: datetime
```
Then:
- `schemas/auth.py` `ProvisionOrgIn` ([auth.py:40-61](apps/api/schemas/auth.py#L40-L61)): replace `plivo_phone_number`/`twilio_phone_number` with `phone_numbers: list[OrgPhoneNumberIn] = []`. `routers/auth.py provision_org` (lines 105-185) calls `replace_org_phone_numbers` after creating the `Org` row.
- `schemas/billing.py` `OrgUpdateIn`/`OrgAdminOut`: replace the two scalar fields with `phone_numbers: list[OrgPhoneNumberIn] | None = None` (omitted = untouched, matching today's `exclude_unset` semantics) on the input, and `phone_numbers: list[OrgPhoneNumberOut]` on the output. `routers/billing.py update_org` (lines 225-293) and `list_orgs` build the output from the new relationship; `update_org` calls `replace_org_phone_numbers` only when `phone_numbers` was provided.
- `schemas/admin.py` `OrgNumbersIn`/`OrgNumbersOut` (lines 77-96): same shape change. `routers/admin.py get_org_numbers`/`update_org_numbers` (lines 1896-1961) updated accordingly — kept in sync even though its write side has no frontend caller yet, since its read side (`GET /admin/org-numbers`) feeds `calling/page.tsx`.

### 6. Tests
Existing coverage that references these fields/endpoints and needs updating: `test_admin_endpoints.py`, `test_auth_endpoints.py`, `test_platform_admin.py`, `test_voice_webhook_org_resolution.py`, `test_campaign_dialer.py`, `test_follow_ups_endpoints.py`, `test_tools.py`, `test_realtime_bridge_instructions.py`. Add/extend cases for: creating an org with 2+ numbers on one provider, updating an org to add/remove numbers, inbound webhook resolving the right org when it owns multiple numbers on a provider, outbound call/campaign dial using the *default* number when an org has several.

## Frontend

### 1. Types — `apps/web/src/lib/types.ts`
Add:
```ts
export interface OrgPhoneNumber {
  id: string;
  provider: "plivo" | "twilio";
  phone_number: string;
  is_default: boolean;
  created_at: string;
}
```
`OrgNumbers` (lines 227-234) drops the two scalar fields for `phone_numbers: OrgPhoneNumber[]` (also fixes the stale "only one is ever set" comment, which already contradicts actual behavior).

### 2. Hooks
- `useAdminOrgs.ts`: `AdminOrg` drops the two fields for `phone_numbers: OrgPhoneNumber[]`; `ProvisionOrgInput`/`UpdateOrgInput` get `phone_numbers?: { provider: "plivo" | "twilio"; phone_number: string; is_default?: boolean }[]` (array present = replace org's set, matching the backend's `exclude_unset` semantics; omit the key entirely to leave numbers untouched on update).
- `useOrgNumbers.ts`: same shape change on `OrgNumbers`.

### 3. New shared component — `apps/web/src/components/organizations/phone-number-list-field.tsx`
One reusable field for "this provider's list of numbers": renders existing entries (masked/formatted number + a small "Primary" badge on the default one + a remove ✕ button), an "+ Add number" row (text input validated against a shared E.164 regex, Enter or button appends), and a "Set primary" text action on non-default rows once 2+ exist. Props: `provider: "plivo" | "twilio"`, `value: PhoneNumberEntry[]`, `onChange`. Extract the currently-duplicated `E164_REGEX`/`E164_MESSAGE` (present verbatim in both `new-org-dialog.tsx` and `edit-org-dialog.tsx`) into a small shared constant this component and both dialogs import, instead of a third copy.

### 4. Dialogs
- `new-org-dialog.tsx`: replace the two single `<Input>` fields (lines ~251-292) with `<PhoneNumberListField provider="plivo" .../>` and `<PhoneNumberListField provider="twilio" .../>`; submit payload builds `phone_numbers` by concatenating both lists with their `provider` tag.
- `edit-org-dialog.tsx`: same swap; `formFromOrg` seeds each list by filtering `org.phone_numbers` on `provider`.

### 5. Consumer — `apps/web/src/app/(dashboard)/calling/page.tsx`
`hasBothProviders` (line 48) changes from reading the two scalar fields to `orgNumbers?.phone_numbers.some(n => n.provider === "plivo")` / `"twilio"` — no other change needed there since it only cares about presence, not count.

## Verification
- Backend: `pytest apps/api/tests` (full suite, since the model/schema change is load-bearing across many files) — specifically the 8 test files listed above.
- Run the new migration up and down against a local Postgres to confirm the backfill/rollback round-trips correctly with real org rows that have Plivo-only, Twilio-only, both, and neither.
- Frontend: `npx tsc --noEmit` in `apps/web`.
- Manual: start the dev server, open the platform-admin Organizations page, create an org with 2 Plivo numbers + 1 Twilio number, verify it saves and the edit dialog reopens showing all 3 with the right one marked primary; add/remove a number on an existing org; confirm the org's own `/calling` page still shows the provider picker correctly.
