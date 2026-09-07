# Campaign creator attribution + member-scoped visibility & escalation routing

## Context

Campaigns are currently org-wide only. `call_campaigns` has just `org_id` — no record of
*who* created a campaign — and `GET /admin/campaigns` returns every campaign in the org
([admin.py:1740](apps/api/routers/admin.py#L1740)). So a team member who has never run a
campaign still sees (and can act on) everyone else's campaigns, and escalations raised from
any campaign's AI call/chat get round-robined across the whole team.

This mirrors a gap that was already solved for **Contacts** (`Contact.created_by_account_user_id`,
siloed per creator — [crm.py:49](apps/api/routers/crm.py#L49)) and **Leads**
(`Lead.claimed_by_account_user_id` + `_member_lead_scope` — a `role=="member"` caller only
sees rows assigned to them, admins/superusers see all — [admin.py:632](apps/api/routers/admin.py#L632)).

Desired outcome:
1. Every campaign records its creator.
2. A `role=="member"` caller sees and can act on **only campaigns they created**; org admins,
   platform superusers, and `X-Admin-Token` callers see all (same rule as leads).
3. When an AI call/chat **from a campaign** escalates to a human, the escalation `Lead` is
   assigned to that campaign's creator and only they are WhatsApp-notified — round-robin is
   skipped. Non-campaign escalations keep the existing round-robin. If a campaign has no
   creator on file (pre-migration rows), fall back to round-robin.

## 1. Migration — add the column

New file `migrations/versions/<rev>_add_campaign_created_by.py` (down_revision = current head
`a9b0c1d2e3f4`; follow the style of
[b2c3d4e5f6a7_add_org_preferred_voice_provider.py](migrations/versions/b2c3d4e5f6a7_add_org_preferred_voice_provider.py)):

```python
op.add_column('call_campaigns', sa.Column(
    'created_by_account_user_id', sa.UUID(),
    sa.ForeignKey('account_users.id', ondelete='SET NULL'), nullable=True))
```

Nullable + `SET NULL` on delete — matches how `call_campaigns.script_id` / `phone_number_id`
already handle a deleted parent, and lets every existing campaign stay valid with a NULL
creator.

## 2. Model

[apps/api/db/models/call_campaign.py](apps/api/db/models/call_campaign.py) — add:

```python
created_by_account_user_id: Mapped[UUID | None] = mapped_column(
    ForeignKey("account_users.id", ondelete="SET NULL"), nullable=True
)
```

with a short docstring: "The account_user who created this campaign. NULL for pre-attribution
rows and X-Admin-Token-created ones. A `role=='member'` caller only sees/acts on campaigns
where this is their id (see admin._member_lead_scope); admins see all. Also decides who a
campaign escalation is routed to (see core/tools.transfer_to_human)."

## 3. Set the creator on every campaign-creation path

Thread a new keyword through the two shared helpers in
[apps/api/routers/admin.py](apps/api/routers/admin.py):

- `_create_campaign_from_rows(...)` ([:1386](apps/api/routers/admin.py#L1386)) — add
  `created_by_account_user_id: UUID | None = None`, pass it into the `CallCampaign(...)`
  constructor ([:1435](apps/api/routers/admin.py#L1435)).
- `_create_campaigns_from_rows(...)` ([:1560](apps/api/routers/admin.py#L1560)) — add the same
  kwarg, forward it in its call to `_create_campaign_from_rows` ([:1625](apps/api/routers/admin.py#L1625)).

Then in the 3 endpoints that create campaigns, add `account_user_id: RequestAccountUserDep`
([deps.py:393](apps/api/deps.py#L393) — resolves to the session's account_user, or
`DEFAULT_OWNER_ID` for admin-token) and pass `created_by_account_user_id=account_user_id`:

- `create_campaign` — `POST /admin/campaigns` ([:1647](apps/api/routers/admin.py#L1647))
- `import_leads_file` — `POST /admin/leads/import` ([:1145](apps/api/routers/admin.py#L1145))
- `import_leads_bulk` — `POST /admin/leads/bulk` ([:1236](apps/api/routers/admin.py#L1236))

## 4. Member-scope the read + action endpoints

Reuse `_member_lead_scope(scope_org_id, org, payload)` as-is — its logic is already generic
("member → own account_user_id, everyone else → None"), only its name says "lead". Add a
one-line comment there noting it now also scopes campaigns.

Swap `org: RequestOrgDep` for the `scope_org_id: AnalyticsScopeDep, org: CurrentOrgDep,
payload: SessionPayloadDep` trio (exactly as `list_leads` does —
[admin.py:657](apps/api/routers/admin.py#L657)) in:

- **`list_campaigns`** ([:1740](apps/api/routers/admin.py#L1740)) — after the existing
  `.where(CallCampaign.org_id == ...)`, add
  `if (s := _member_lead_scope(scope_org_id, org, payload)) is not None:
  stmt = stmt.where(CallCampaign.created_by_account_user_id == s)`.
- **`get_reports_campaigns`** — `GET /admin/reports/campaigns`
  ([:399](apps/api/routers/admin.py#L399)) — same filter on its `select(CallCampaign)`.

For the single-campaign endpoints that currently do `db.get(CallCampaign, campaign_id)` then
check nothing about the caller — `get_campaign` ([:1838](apps/api/routers/admin.py#L1838)),
`update_campaign` ([:1882](apps/api/routers/admin.py#L1882)), `pause` ([:1910](apps/api/routers/admin.py#L1910)),
`resume` ([:1924](apps/api/routers/admin.py#L1924)), `schedule` ([:1944](apps/api/routers/admin.py#L1944)):
add the `CurrentOrgDep` + `SessionPayloadDep` (+ `AnalyticsScopeDep` where the org check needs
it) params and, right after loading the campaign, a shared guard:

```python
scope = _member_lead_scope(scope_org_id, org, payload)
if scope is not None and campaign.created_by_account_user_id != scope:
    raise HTTPException(status_code=404, detail="Campaign not found")
```

404 (not 403) to match how members are kept from even knowing other campaigns exist. Factor
this into a small local helper `_guard_campaign_access(campaign, scope)` to avoid repeating it
5×.

## 5. Expose the creator on `CampaignOut` (for the admin view)

[apps/api/schemas/campaign.py](apps/api/schemas/campaign.py) — add to `CampaignOut`:
`created_by_account_user_id: UUID | None = None` and `created_by_name: str | None = None`.
`_campaign_out` ([admin.py:1365](apps/api/routers/admin.py#L1365)) already builds the DTO — it
needs the creator's display name. In `list_campaigns` / `_campaign_counts_bulk` area, do one
extra `select(AccountUser.id, AccountUser.name).where(AccountUser.id.in_(creator_ids))` and map
it in (same batch-lookup style the counts subqueries already use). Frontend then shows a
"Created by" column when the list contains more than the current user's own campaigns (admins);
[apps/web/src/components/campaigns/campaigns-view.tsx](apps/web/src/components/campaigns/campaigns-view.tsx)
+ the `Campaign` type in [apps/web/src/lib/types.ts](apps/web/src/lib/types.ts). Members'
lists are already all-their-own so the column can stay hidden for them.

## 6. Route campaign escalations to the campaign creator

[apps/api/core/tools.py](apps/api/core/tools.py) `transfer_to_human` ([:710](apps/api/core/tools.py#L710)).

`CampaignTarget.conversation_id` is set for **both** channels — the voice bridge via
`attach_campaign_conversation` ([adapter.py:145](apps/api/channels/voice/adapter.py#L145)) and
WhatsApp via [agent.py:308](apps/api/core/agent.py#L308). `transfer_to_human` already receives
`conversation_id`. So:

1. Near the top, if `conversation_id` is set, look up the campaign creator:
   ```python
   campaign_owner_id: UUID | None = None
   if conversation_id is not None:
       target = (await db.execute(
           select(CampaignTarget).where(CampaignTarget.conversation_id == conversation_id)
       )).scalar_one_or_none()
       if target is not None:
           campaign = await db.get(CallCampaign, target.campaign_id)
           if campaign is not None:
               campaign_owner_id = campaign.created_by_account_user_id
   ```
2. In the notify block ([:756-795](apps/api/core/tools.py#L756)): when `campaign_owner_id` is
   set, skip the round-robin `INCR` — set `notify_account_user_id = campaign_owner_id` and
   resolve `notify_phone` from that member's `AccountUser.mobile` (look it up, or pick it out
   of `_resolve_team_notify_targets`'s list by id). If that member has no mobile, still assign
   the Lead to them (below) but log `transfer_to_human_campaign_owner_no_mobile` and send no
   WhatsApp — do **not** fall back to round-robin (that would defeat the routing).
3. The `Lead(...)` write ([:814](apps/api/core/tools.py#L814)) already sets
   `claimed_by_account_user_id=notify_account_user_id` — so once step 2 sets that variable, the
   lead lands on the right member's dashboard automatically. No change to the Lead block.
4. When `campaign_owner_id` is `None` (not a campaign, or a pre-migration campaign) the
   existing round-robin path runs unchanged.

Update the `transfer_to_human` docstring to describe the campaign-owner override.

## Tests

- [apps/api/tests/test_campaign_endpoints.py](apps/api/tests/test_campaign_endpoints.py) — a
  `role=="member"` session sees only its own campaigns in `GET /admin/campaigns`; gets 404 on
  another member's campaign detail / pause / resume / schedule / update; an admin session sees
  all. Creator id is persisted on create via each of the 3 entry points.
- [apps/api/tests/test_tools.py](apps/api/tests/test_tools.py) — `transfer_to_human` with a
  `conversation_id` belonging to a campaign target assigns the escalation `Lead` to the
  campaign's `created_by_account_user_id` and notifies that member (not round-robin); with a
  non-campaign `conversation_id` it still round-robins; campaign with NULL creator round-robins.
- [apps/api/tests/test_campaign_dialer.py](apps/api/tests/test_campaign_dialer.py) /
  `test_admin_endpoints.py` — existing campaign tests still pass with the new nullable column
  and the extra deps.

## Verification

1. `cd apps/api && alembic upgrade head` against a scratch DB — column added, existing rows
   NULL.
2. `pytest apps/api/tests/test_campaign_endpoints.py apps/api/tests/test_tools.py
   apps/api/tests/test_campaign_dialer.py apps/api/tests/test_admin_endpoints.py`.
3. Manual (local, two sessions):
   - As member A: upload a campaign → appears in A's list. As member B: list is empty of A's
     campaign; `GET /admin/campaigns/<A's id>` → 404. As the owner/admin: both campaigns show,
     with a "Created by" column.
   - Run a 1-row voice campaign owned by member A to a test number, have the AI transfer to a
     human → escalation Lead shows on A's Leads/Escalations dashboard and only A's mobile gets
     the WhatsApp notice.
   - A normal inbound WhatsApp/voice escalation (no campaign) still round-robins.
4. Frontend build: `cd apps/web && pnpm build` (or `pnpm lint`) after the type + column change.
