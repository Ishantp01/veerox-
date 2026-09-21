"""grant human_support and follow_up_tasks to orgs with an explicit feature list,
and drop the removed "helpdesk" feature key from every org

Those two features are new. Orgs whose ``enabled_features`` is NULL are
unrestricted and pick them up automatically; orgs with an explicit list would
silently lose Human Support and follow-up reminders they use today, so append
both keys to keep their current access (a platform admin can then untick them).

Revision ID: c3d4e5f6a7b8
Revises: a1c2e3f4b5d6
Create Date: 2026-09-21 00:00:00.000000

"""
from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'a1c2e3f4b5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_FEATURES = ("human_support", "follow_up_tasks")
# Removed feature (the in-app help-desk chatbot): update_org rejects unknown keys,
# so a stale "helpdesk" in a stored list would make editing that org fail.
REMOVED_FEATURES = ("helpdesk",)


def _rows(conn):
    return conn.execute(sa.text("SELECT id, enabled_features FROM orgs WHERE enabled_features IS NOT NULL")).fetchall()


def upgrade() -> None:
    conn = op.get_bind()
    for org_id, features in _rows(conn):
        current = json.loads(features) if isinstance(features, str) else list(features or [])
        merged = [f for f in current if f not in REMOVED_FEATURES]
        merged += [f for f in NEW_FEATURES if f not in merged]
        if merged != current:
            conn.execute(
                sa.text("UPDATE orgs SET enabled_features = CAST(:f AS JSON) WHERE id = :id"),
                {"f": json.dumps(merged), "id": org_id},
            )


def downgrade() -> None:
    conn = op.get_bind()
    for org_id, features in _rows(conn):
        current = json.loads(features) if isinstance(features, str) else list(features or [])
        kept = [f for f in current if f not in NEW_FEATURES]
        if kept != current:
            conn.execute(
                sa.text("UPDATE orgs SET enabled_features = CAST(:f AS JSON) WHERE id = :id"),
                {"f": json.dumps(kept), "id": org_id},
            )
