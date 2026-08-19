"""add unique constraints to follow_up_tasks

Prevents duplicate reminder tasks under horizontal scaling (see
longrunning/operations/load.md): workers/follow_up_dispatcher.py's
materialize functions used a "check if a task already exists, then insert
if not" pattern with no lock between the check and the insert. Two
dispatcher instances polling at the same moment could both see "no task
yet" and both create one, sending the same reminder twice.

Two constraints, matching the two materialize functions:
  - One lead-triggered task per lead at most (rule_id IS NULL AND
    template_name IS NULL — the template_name check matters: core/tools.py's
    book_appointment ALSO creates multiple rule_id=NULL tasks per lead on
    purpose, one per reminder offset, but always with template_name set
    ("appointment_reminder"/"appointment_confirmation"). Confirmed against
    production data before writing this — every existing rule_id=NULL
    lead with >1 task was a legitimate book_appointment case, all with
    template_name set; a bare "one rule_id IS NULL task per lead"
    constraint would have broken that feature.).
  - One rule-triggered task per (lead_id, rule_id) pair at most.

Both are partial unique indexes rather than a single constraint on
(lead_id, rule_id) directly, since a plain unique constraint treats every
NULL as distinct (would not have caught the rule_id IS NULL case).

Revision ID: e7b3a5c9f2d4
Revises: b3f8a1d2c4e6
Create Date: 2026-08-19 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'e7b3a5c9f2d4'
down_revision: Union[str, None] = 'b3f8a1d2c4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_follow_up_tasks_lead_builtin",
        "follow_up_tasks",
        ["lead_id"],
        unique=True,
        postgresql_where="rule_id IS NULL AND template_name IS NULL",
        sqlite_where="rule_id IS NULL AND template_name IS NULL",
    )
    op.create_index(
        "uq_follow_up_tasks_lead_rule",
        "follow_up_tasks",
        ["lead_id", "rule_id"],
        unique=True,
        postgresql_where="rule_id IS NOT NULL",
        sqlite_where="rule_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_follow_up_tasks_lead_rule", table_name="follow_up_tasks")
    op.drop_index("uq_follow_up_tasks_lead_builtin", table_name="follow_up_tasks")
