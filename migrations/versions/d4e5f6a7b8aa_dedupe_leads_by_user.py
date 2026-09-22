"""dedupe leads by (org_id, user_id) and enforce one lead per customer

Leads used to be created any time the AI called its capture_lead tool, deduped
only by a 10-minute Redis lock on (org_id, phone, intent) — so the same phone
number calling/messaging again later, or with different intent wording, could
end up with several separate Lead rows even though the underlying User (and
its conversation history) was already one row per (org_id, phone). This
migration merges any existing duplicates and adds a DB-level unique
constraint on (org_id, user_id) so it can't happen again.

For each (org_id, user_id) group with more than one Lead, the earliest-created
row survives: appointments pointing at a duplicate are repointed to it, an
unclaimed survivor inherits a duplicate's claim, and the duplicates are then
deleted. FollowUpTask.lead_id is NOT NULL with ondelete=CASCADE, so any
follow-ups tied to a deleted duplicate go with it — acceptable since
duplicates are artifacts of the old dedup bug, not leads anyone was actively
working (the survivor's own follow-ups are untouched).

Revision ID: d4e5f6a7b8aa
Revises: c3d4e5f6a7b8
Create Date: 2026-09-22 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8aa'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        WITH ranked AS (
            SELECT id, org_id, user_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY org_id, user_id ORDER BY created_at ASC, id ASC
                   ) AS rn
            FROM leads
        ),
        survivors AS (
            SELECT org_id, user_id, id AS survivor_id FROM ranked WHERE rn = 1
        )
        UPDATE leads AS l
        SET claimed_by_account_user_id = dup.claimed_by_account_user_id,
            claimed_at = dup.claimed_at
        FROM ranked AS r
        JOIN survivors AS s ON s.org_id = r.org_id AND s.user_id = r.user_id
        JOIN leads AS dup ON dup.id = r.id
        WHERE l.id = s.survivor_id
          AND r.rn > 1
          AND l.claimed_by_account_user_id IS NULL
          AND dup.claimed_by_account_user_id IS NOT NULL
    """))

    conn.execute(sa.text("""
        WITH ranked AS (
            SELECT id, org_id, user_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY org_id, user_id ORDER BY created_at ASC, id ASC
                   ) AS rn
            FROM leads
        ),
        survivors AS (
            SELECT org_id, user_id, id AS survivor_id FROM ranked WHERE rn = 1
        )
        UPDATE appointments AS a
        SET lead_id = s.survivor_id
        FROM ranked AS r
        JOIN survivors AS s ON s.org_id = r.org_id AND s.user_id = r.user_id
        WHERE a.lead_id = r.id AND r.rn > 1
    """))

    conn.execute(sa.text("""
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY org_id, user_id ORDER BY created_at ASC, id ASC
                   ) AS rn
            FROM leads
        )
        DELETE FROM leads WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
    """))

    op.create_unique_constraint("uq_leads_org_user", "leads", ["org_id", "user_id"])


def downgrade() -> None:
    op.drop_constraint("uq_leads_org_user", "leads", type_="unique")
