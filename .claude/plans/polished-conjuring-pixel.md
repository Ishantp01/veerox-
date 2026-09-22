# One lead per phone number, delete lead, full-field search

## Context

Today a `Lead` row is only written when the AI decides to call the internal
`capture_lead` tool during a conversation — a call or WhatsApp message that
doesn't trigger that tool never shows up on the leads page. Worse, dedup is
only a 10-minute Redis lock on `(org_id, phone, intent)`
([tools.py:543-600](apps/api/core/tools.py#L543-L600)), so the same phone
number calling again later, or with different intent wording, creates a
second `Lead` row — even though the underlying `User` (and therefore the
conversation history) is already correctly deduped by `(org_id, phone)` via
`_get_or_create_user_by_phone`. There's also no way to delete a lead, and the
leads-page search box only matches `intent`/`tags`
([admin.py:727-731](apps/api/routers/admin.py#L727-L731)), not name or phone.

Goal: every call and WhatsApp message guarantees a lead exists, exactly one
lead per phone number ever, a working delete action, and search across all
lead fields.

## 1. Make the Lead unique per customer at the DB level

`Lead.user_id` already points at the same deduped `User` row every channel
resolves to (`_get_or_create_user_by_phone` in tools.py, and each channel's
own local copy in `channels/whatsapp/adapter.py:271-285` and
`channels/voice/adapter.py:677-685`). So the correct dedup key is
`(org_id, user_id)`, not phone text — it inherits the phone-normalization
work `User` already does and it's what `Lead`'s own FK is built on.

**New migration** (`migrations/versions/<rev>_dedupe_leads_by_user.py`,
`down_revision = 'c3d4e5f6a7b8'`, following the structure of
`f7e8a9b0c1d2_add_lead_claim_fields.py` and the data-fixup style of
`c3d4e5f6a7b8_grant_new_features_to_restricted_orgs.py`):

1. Data backfill — for every `(org_id, user_id)` group with more than one
   `Lead` row: keep the row with the earliest `created_at` as the survivor.
   - Copy `claimed_by_account_user_id`/`claimed_at` onto the survivor if the
     survivor is unclaimed and a duplicate is claimed.
   - Repoint `Appointment.lead_id` from duplicates to the survivor's id
     (`ondelete="SET NULL"`, no constraint conflict — plain `UPDATE`).
   - Delete the duplicate `Lead` rows. `FollowUpTask.lead_id` is `NOT NULL,
     ondelete="CASCADE"` ([follow_up.py:103](apps/api/db/models/follow_up.py#L103))
     so any pending follow-ups tied to a *duplicate* lead are cascade-deleted
     with it — acceptable, since duplicates are noise from the old dedup bug,
     and the survivor keeps its own follow-ups untouched.
   - All of this as `op.execute(sa.text(...))` statements, matching the
     existing data-migration convention.
2. `op.create_unique_constraint("uq_leads_org_user", "leads", ["org_id", "user_id"])`.
3. `downgrade()` just drops the constraint (data merge isn't reversible).

## 2. Shared get-or-create-lead helper

Add `get_or_create_lead_for_user` next to `_get_or_create_user_by_phone` in
[apps/api/core/tools.py](apps/api/core/tools.py#L510-L534), same
select-then-insert-with-flush shape, relying on the new unique constraint for
concurrency safety exactly like the user helper's docstring already
describes for `users(org_id, phone)`:

```python
async def get_or_create_lead_for_user(
    db: AsyncSession, org_id: UUID, user: User, *, channel: str | None = None,
    intent: str | None = None,
) -> Lead:
    existing = (await db.execute(
        select(Lead).where(Lead.org_id == org_id, Lead.user_id == user.id)
    )).scalar_one_or_none()
    if existing is not None:
        if intent and not existing.intent:
            existing.intent = intent
        if channel and not existing.channel:
            existing.channel = channel
        return existing
    lead = Lead(org_id=org_id, user_id=user.id, name=user.name, phone=user.phone,
                channel=channel, intent=intent)
    db.add(lead)
    await db.flush()
    return lead
```

## 3. Wire it into every lead-creation path

- **WhatsApp** — [channels/whatsapp/adapter.py:344](apps/api/channels/whatsapp/adapter.py#L344)
  (`process_inbound`, right after `_get_or_create_user`): call
  `get_or_create_lead_for_user(db, org_id, user, channel="whatsapp")` before
  the commit at line 368, so every inbound message guarantees a lead.
- **Voice** — [channels/voice/adapter.py:706](apps/api/channels/voice/adapter.py#L706)
  (`open_voice_conversation`, right after its own `_get_or_create_user`):
  same call with `channel="voice"`, before the commit at line 711.
- **`capture_lead` tool** ([tools.py:543](apps/api/core/tools.py#L543)):
  replace the Redis-lock-then-`INSERT` body with
  `get_or_create_lead_for_user(...)`, updating `intent`/`name` on the
  existing row when the AI supplies a more specific intent. Drop the now
  -redundant `_lead_dedupe_key`/Redis logic — the DB constraint is the
  source of truth now.
- **`_claim_customer_lead_for_member`** ([admin.py:801-833](apps/api/routers/admin.py#L801-L833)):
  simplify to call the same helper instead of its own "most recent lead"
  query — behaviorally identical now that there's only ever one lead per
  user, but removes the duplicated logic.
- **Bulk import** (`_create_campaign_from_rows`, [admin.py:~1825-1845](apps/api/routers/admin.py#L1825-L1845)):
  switch its unconditional `db.add(Lead(...))` to the same helper so
  re-uploading a CSV with an existing customer's number updates that lead
  instead of erroring on the new constraint or creating a duplicate.
- **`appointments.py:171`** (`new_lead`) and **`crm.py:164`**
  (`create_lead_from_contact`): same swap, for consistency and to avoid
  `IntegrityError` on the new constraint.
- **`routers/leads.py` `POST /leads`** ([leads.py:30-48](apps/api/routers/leads.py#L30-L48)):
  same swap — it's a separate, mounted, low-traffic API-style creation
  endpoint; keep it working under the new constraint.

## 4. `DELETE /admin/leads/{lead_id}`

Add next to `update_lead` in [admin.py](apps/api/routers/admin.py#L1032),
mirroring `delete_contact`'s scope checks
([crm.py:364-378](apps/api/routers/crm.py#L364-L378)) and `get_lead`'s
org/member-scope 404 pattern ([admin.py:980-1000](apps/api/routers/admin.py#L980-L1000)):

```python
@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: UUID, db: DbDep, scope_org_id: ..., org: ..., payload: ...):
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if lead is None: raise HTTPException(404, "Lead not found")
    if scope_org_id is not None and lead.org_id != scope_org_id: raise HTTPException(404, ...)
    member_scope = _member_lead_scope(scope_org_id, org, payload)
    if member_scope is not None and lead.claimed_by_account_user_id != member_scope:
        raise HTTPException(404, ...)
    await db.delete(lead)
    await db.commit()
    return {"ok": True}
```

`FollowUpTask` rows cascade-delete with it; `Appointment.lead_id` just goes
null — same trade-off as duplicate-merge above, and consistent with how
`delete_contact` already treats its dependents.

## 5. Broaden search to all fields

Extend `_lead_search_clause` ([admin.py:727-731](apps/api/routers/admin.py#L727-L731))
to OR across name, phone, intent, tags, status, qualification_status:

```python
def _lead_search_clause(search: str):
    like = f"%{search}%"
    return or_(
        Lead.name.ilike(like), Lead.phone.ilike(like), Lead.intent.ilike(like),
        Lead.status.ilike(like), Lead.qualification_status.ilike(like),
        cast(Lead.tags, String).ilike(like),
    )
```

This single function already backs both `GET /admin/leads` (line 906) and
`GET /admin/leads.csv`/`.xlsx` (line 1134+), so one change covers list +
export. Update the doc comment on `search` ([admin.py:885](apps/api/routers/admin.py#L885)
and 1183) plus the frontend placeholder/comment (below).

## 6. Frontend

- **`apps/web/src/lib/hooks/useLeads.ts`**: add `useDeleteLead()` mirroring
  `useDeleteContact()` (`useContacts.ts:103-112`) — `DELETE /admin/leads/{id}`,
  invalidate `["leads"]` on success.
- **`apps/web/src/components/leads/lead-detail.tsx`**: add a "Delete lead"
  button in the `PageHeader` action slot (next to the `ChannelBadge`,
  ~line 249), using `useConfirm()` + toast + `Trash2` icon, exactly the
  pattern in `apps/web/src/components/crm/contact-detail.tsx:47-98`. On
  success, navigate back (`backHref`) since the detail page no longer exists.
- **`apps/web/src/components/leads/lead-table.tsx`**: add a per-row delete
  action (icon button, `e.stopPropagation()` before the mutate call) next to
  the existing "Conversation"/"Human Support" row actions
  (lines 131-142/164-178 pattern), so leads can be deleted straight from the
  list too.
- **`apps/web/src/components/leads/leads-view.tsx`**: update the search
  input placeholder/aria-label (lines ~225-238) from "Search intent or
  tag…" to "Search name, phone, intent, tag…", and update the explanatory
  comment above the debounce effect (lines 120-123).

Since `/leads`, `/crm/leads`, `/calling/leads`, `/whatsapp/leads` all render
the same `LeadsView`/`LeadTable`/`LeadDetail` components, these changes apply
everywhere automatically.

## Verification

1. `alembic upgrade head` locally against a dev DB seeded with duplicate
   leads for one phone number — confirm only one survives, `Appointment`
   rows keep a valid `lead_id`, and the unique constraint is present.
2. Backend: run `pytest apps/api/tests/test_admin_endpoints.py
   apps/api/tests/test_tools.py apps/api/tests/test_crm_endpoints.py
   apps/api/tests/test_appointments_router.py` — update/extend tests that
   construct multiple `Lead` rows for the same `user_id` (several exist in
   `test_admin_endpoints.py`, e.g. line 222-223, 1157-1158) since those will
   now violate the unique constraint; add a new test asserting a second
   inbound WhatsApp/voice event for the same phone reuses the existing lead,
   and a `test_delete_lead` covering the new endpoint and its scope checks.
3. Manual: send two WhatsApp messages and place two calls from the same
   number through the dev environment; confirm exactly one lead appears with
   both conversations attached, then delete it from the UI and confirm it
   disappears and re-messaging recreates a fresh single lead. Try the search
   box with a name and a phone substring.
