# Per-team-member scoping for appointments & conversations

## Context

Today the dashboard already silos **leads** and **campaigns** by team member: a
`role=="member"` account only sees leads assigned to them
(`Lead.claimed_by_account_user_id`) and campaigns they created, while an
`admin` / platform superuser / `X-Admin-Token` caller sees the whole org. This
is implemented in `apps/api/routers/admin.py::_member_lead_scope`. **Contacts**
go further and are siloed by creator for everyone (`routers/crm.py`).

**Appointments and conversations have no such scoping.** Every team member
currently sees every appointment (`routers/appointments.py`, org-filter only)
and every conversation (`routers/admin.py::list_conversations` +
`/conversations/{id}/messages` + `/summarize`, and the largely-unused
`routers/conversations.py`). The user wants each member to see only *their*
appointments and conversations, with admins still seeing all — matching how
leads already behave.

## Approach: reuse lead ownership (no new columns, no migration)

A conversation or appointment "belongs to" the member who owns the underlying
customer's lead:

- **Conversation** shares `user_id` with `Lead`. A member sees a conversation
  when some `Lead` for that `user_id` has `claimed_by_account_user_id == me`.
- **Appointment** has `lead_id` (and `contact_id`). A member sees an appointment
  when its `Lead` is claimed by them, **or** its `Contact` was created by them
  (`Contact.created_by_account_user_id`, already member-siloed).
- Items linked to no member-owned lead/contact are **hidden from members**;
  admins/superusers/`X-Admin-Token` see everything (unchanged).

This keeps one ownership concept across leads, campaigns, appointments and
conversations, and needs no schema change.

## Changes

### 1. Shared scope dependency — `apps/api/deps.py`

Add `resolve_member_scope_account_user_id(db, payload, x_admin_token) -> UUID | None`
and `MemberScopeDep = Annotated[UUID | None, Depends(...)]`:

- `X-Admin-Token` match → `None` (see all).
- no session payload → `None`.
- load `AccountUser`; `is_superuser` → `None`.
- load `OrgMembership` for `(account_user_id, org_id)`; `role == "admin"` → `None`;
  otherwise return `UUID(payload["account_user_id"])`.

Mirrors the existing logic in `admin.py::_member_lead_scope` /
`resolve_analytics_scope_org_id` but usable from routers that only depend on
`verify_admin_or_session` + `RequestOrgDep`.

Add a small helper (same module) to build the reusable subquery:
`owned_lead_user_ids(scope)` → `select(Lead.user_id).where(Lead.claimed_by_account_user_id == scope)`
and `owned_lead_ids(scope)` → `select(Lead.id).where(...)`.

### 2. Appointments — `apps/api/routers/appointments.py`

- `list_appointments`: when `MemberScopeDep` is not `None`, add
  `.where(or_(Appointment.lead_id.in_(owned_lead_ids(scope)),
             Appointment.contact_id.in_(select(Contact.id).where(
                 Contact.created_by_account_user_id == scope))))`.
- `get_appointment` / `update_appointment`: after loading the row, if scope is
  not `None` and the appointment is not in that member's set → `404`
  (never 403, matching `_guard_campaign_access`).
- `create_appointment`: when the caller is a member and books against a `Lead`
  that is currently unclaimed, set `lead.claimed_by_account_user_id = scope` /
  `claimed_at = now()` so the creator keeps visibility (same self-claim
  semantics as `admin.py` escalation claim). Contact-path bookings already
  create a `Lead`; set its `claimed_by_account_user_id` to the member there too.

### 3. Conversations — `apps/api/routers/admin.py`

- `list_conversations`: add `org: CurrentOrgDep` and `payload: SessionPayloadDep`
  params (both already imported/used elsewhere in the file), compute
  `member_scope = _member_lead_scope(scope_org_id, org, payload)`, and when not
  `None` add `.where(Conversation.user_id.in_(owned_lead_user_ids(member_scope)))`.
- `get_conversation_messages`: currently takes no org/session context — add the
  same deps, load the `Conversation`, and 404 for a member when
  `conversation.user_id` is not in their owned-lead user ids.
- `summarize_conversation`: same guard after the existing `db.get(Conversation, …)`.
- `get_lead` (lead detail, line ~800): already lead-scoped by `_member_lead_scope`
  via `_guard`, so its embedded conversation list needs no extra work — verify.

### 4. Standalone `apps/api/routers/conversations.py`

Frontend uses `/admin/conversations*` (see `apps/web/src/lib/hooks/useConversations.ts`),
not this router, but it is still registered in `main.py`. Apply the same member
filter to `list_conversations` / `get_transcript` here using `MemberScopeDep`,
for consistency and to avoid a scoping bypass.

### 5. Follow-up tasks — `apps/api/routers/follow_ups.py` (same rule, low effort)

`FollowUpTask.lead_id` ties each task to a lead. In `list_follow_up_tasks` add
`.where(FollowUpTask.lead_id.in_(owned_lead_ids(scope)))` when
`MemberScopeDep` is set; 404-guard `cancel_follow_up_task`. Rules
(`/follow-up-rules`) stay org-wide (config, not per-member data).

### 6. Frontend (copy only, optional)

`apps/web/src/app/(dashboard)/conversations/page.tsx` and
`crm/appointments/page.tsx` descriptions say "All conversations…" / list all —
soften to "Your …" for members using the existing `role` from
`apps/web/src/lib/auth-context.tsx`. No API-call changes; endpoints simply
return the caller's own rows.

## Out of scope

- No new DB columns or Alembic migration.
- Dashboard summary/analytics tiles (`admin.py` stats endpoints) — left as a
  follow-up; they already use `AnalyticsScopeDep` for org scoping.
- Sales/pipeline router.

## Verification

- `cd apps/api && pytest tests/test_appointments_router.py tests/test_admin_endpoints.py tests/test_router_auth_guard.py`
- New tests to add (mirror member/admin patterns in `tests/test_admin_endpoints.py`):
  - member sees only appointments whose lead they claimed; admin sees all.
  - member gets 404 on another member's appointment `GET`/`PATCH`.
  - member booking an unclaimed lead's appointment auto-claims the lead.
  - member's `/admin/conversations` list excludes conversations for
    non-owned leads; `/messages` and `/summarize` 404 for those.
  - `X-Admin-Token` and `is_superuser` session still see everything.
- Manual: log in as an invited `member`, confirm Appointments and Conversations
  pages show only rows tied to leads assigned to that member; log in as admin,
  confirm full list.
