# Emergency Lead Popup

## Context

When the AI agent can't handle a WhatsApp/voice conversation (angry customer, out-of-scope request, explicit "talk to a human"), it already calls `transfer_to_human` (`apps/api/core/tools.py:566-635`), which writes a `Lead(intent="escalation")` row and pushes an entry onto a Redis list (`human_handoff_queue`). Today the only way a team member finds out is by having the `/escalations` dashboard page open — there's no alert, and no way to claim an escalation so two people don't both jump on it (a stale comment in `admin.py` even claims a "mark handled" endpoint exists; it doesn't — grepped, confirmed absent).

Goal: the instant a new unclaimed escalation exists, anyone on the dashboard sees an unmissable popup with the lead's context and a one-click "Claim" action, so the team actually notices and connects with the lead without polling the page manually.

**Delivery mechanism: polling**, per your choice — this app has zero WebSocket/SSE/pub-sub infra for the dashboard today (the only websocket route is the voice telephony bridge, unrelated). Every other "live" feature here (`useBilling`, `useCampaigns`, `useLeads`, `useConversations`, the existing `useEscalations`) already polls via a centralized `POLL` config in `apps/web/src/lib/query.ts`, on a 3-10s `refetchInterval`. We follow that exact convention instead of building new push infra.

## Backend changes

**1. Migration** (new file in `migrations/versions/`, follow `e1f2a3b4c5d6_add_support_tickets.py`'s style)
- `down_revision = 'e1f2a3b4c5d6'` (confirmed current head — no other migration points to it)
- `op.add_column('leads', sa.Column('claimed_by_account_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('account_users.id', ondelete='SET NULL'), nullable=True))`
- `op.add_column('leads', sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True))`
- `downgrade()` drops both columns in reverse order.

**2. `Lead` model** (`apps/api/db/models/lead.py`) — add the two new columns (`claimed_by_account_user_id: Mapped[UUID | None]` FK, `claimed_at: Mapped[datetime | None]`), matching the existing column style in that file.

**3. `LeadOut` schema** (`apps/api/schemas/lead.py:36`) — add `claimed_by_account_user_id: UUID | None`, `claimed_at: datetime | None`, and a `claimed_by_name: str | None` (populated in the endpoint by joining `AccountUser.full_name`, same idea as `AdminTicketOut.account_user_name` in `apps/api/schemas/support_ticket.py`).

**4. Claim endpoint** — add to `apps/api/routers/admin.py`, right next to the existing `GET /escalations` (`admin.py:1928`), reusing the router's existing `verify_admin_or_session` guard (already applied at the router level, `admin.py:92-94`):

```python
@router.patch("/escalations/{lead_id}/claim", response_model=LeadOut)
async def claim_escalation(
    lead_id: UUID,
    db: DbDep,
    scope_org_id: AnalyticsScopeDep,
    current_user: CurrentUserDep,
    x_admin_token: str | None = Header(None),
) -> LeadOut:
```
- Look up the `Lead` by id, scoped to `scope_org_id` the same way `get_escalations` already is (403/404 if out of scope or missing).
- If `claimed_by_account_user_id` is already set to someone else → `409 Conflict` ("Already claimed by {name}") so two people can't silently overwrite each other.
- Otherwise set `claimed_by_account_user_id = current_user.id`, `claimed_at = now(UTC)`, commit, return the row (joined for `claimed_by_name`).
- `CurrentUserDep` (`deps.py:117`) resolves correctly even under the shared `X-Admin-Token` path (falls back to the default owner account), so this works for both auth modes already supported on this router.

**5. `get_escalations`** (`admin.py:1928-1975`) — no structural change needed; it already returns `recent_leads` via `LeadOut`, which will now carry the new claim fields for free once the schema is updated.

## Frontend changes

**1. Types** (`apps/web/src/lib/types.ts:272-281`) — add `claimed_by_account_user_id?`, `claimed_by_name?`, `claimed_at?` to the `Escalation` shape (and wherever `Lead` is typed).

**2. Claim mutation** — add `useClaimEscalation()` to `apps/web/src/lib/hooks/useEscalations.ts`, a `useMutation` calling `PATCH /admin/escalations/{id}/claim` via the existing `apiFetch`, invalidating `queryKeys.escalations(...)` on success (same pattern as mutations in `useTickets.ts`), and surfacing a `useToast()` confirmation ("You claimed this lead") or the 409 conflict message.

**3. `EmergencyEscalationPopup` component** (new: `apps/web/src/components/escalations/emergency-escalation-popup.tsx`), modeled directly on `apps/web/src/components/billing/credit-expired-modal.tsx` — the existing precedent for a full-screen, unmissable, self-refreshing alert:
- Calls `useEscalations()` with no channel filter (org-wide, both voice + whatsapp), which already polls every 3s (`POLL.escalations`, `query.ts`).
- Keeps a `useRef<Set<string>>` of already-seen entry ids; on each poll, any **new, unclaimed** entry (queue entry, or `recent_leads` row with `claimed_at == null`) gets pushed into a local alert queue — shows one at a time, full-bleed `fixed inset-0 z-[60]` panel (`role="alertdialog"`, focus trap, Escape — same techniques as `CreditExpiredModal`), so it's impossible to miss regardless of which page you're on.
- Content: phone, channel icon, urgency badge, reason, "waiting Xs" live counter, link to the conversation. Two actions: **Claim & Open Conversation** (calls the new mutation, then navigates) and **Snooze** (dismiss for ~60s, reappears if still unclaimed — same snooze pattern as `CreditExpiredModal`, so it's not permanently escapable but doesn't trap the user either).
- If the poll shows the entry got claimed by someone else in the meantime, auto-dismiss with a toast ("Claimed by {name}").
- On a new alert: play a short attention sound (Web Audio API beep, no new asset needed) and, if the tab isn't focused (`document.visibilityState`) and permission was granted, fire a `Notification`. Request `Notification` permission via a small one-time dismissible banner/toast on first mount (must be behind a user gesture in most browsers) rather than an unprompted call — there's no existing convention for this in the codebase, so this is net-new but self-contained.

**4. Mount point** — `apps/web/src/components/layout/dashboard-shell.tsx`: add `<EmergencyEscalationPopup />` as a sibling to the existing `<CreditExpiredModal />` (both outside `<main>`, so neither is clipped by the scroll container and both reach every dashboard route — that's the documented rationale already on that file).

**5. `EscalationTable`** (`apps/web/src/components/escalations/escalation-table.tsx`) — add a "Claimed" column: shows claimer name + relative time if set, otherwise an inline "Claim" button using the same mutation, so the existing `/escalations` page (and its whatsapp/voice variants) also gets ownership visibility, not just the popup.

## Verification

- Run the new migration locally (`alembic upgrade head`) and confirm `leads.claimed_by_account_user_id` / `claimed_at` exist.
- Backend: hit `transfer_to_human` (or insert a test escalation) then `PATCH /admin/escalations/{id}/claim` — confirm 200 + fields set, then confirm a second claim attempt from a different account returns 409.
- Frontend: run the dev server (`npm run dev` in `apps/web`), log in, trigger an escalation (e.g. via the existing test/dev path that calls `transfer_to_human`, or insert a `Lead(intent="escalation")` row directly against the dev DB), and confirm within ~3s the popup appears on an arbitrary dashboard page (not just `/escalations`), sound plays, "Claim" works and dismisses it everywhere, and the `/escalations` table shows the claim.
