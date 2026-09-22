"""add an index on appointments.lead_id

`appointments.lead_id` had no usable index at all (`follow_up_tasks.lead_id`
already has one — `ix_follow_up_tasks_lead_id`, added by
a152273213e2_add_follow_up_rules_and_tasks — so it's left alone here). That
meant `DELETE FROM leads WHERE id = ...` (admin.py's delete_lead) forced
Postgres to seq-scan `appointments` to enforce its `ON DELETE SET NULL`
action — a cause of the delete-lead endpoint being slow at scale.

Revision ID: e5f6a7b8aabb
Revises: d4e5f6a7b8aa
Create Date: 2026-09-22 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'e5f6a7b8aabb'
down_revision: Union[str, None] = 'd4e5f6a7b8aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_appointments_lead_id", "appointments", ["lead_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_appointments_lead_id", table_name="appointments")
