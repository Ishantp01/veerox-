"""add template_params to follow_up_rules

A follow-up rule's template send previously always sent an empty params
list (see workers/follow_up_dispatcher.py's _materialize_rule_tasks), so
only params-free templates like hello_world could be used from the New
Rule dialog — any template with {{1}}/{{2}} placeholders was shown
disabled ("not supported here yet"). This adds the same ordered
per-placeholder token list campaigns already have (FollowUpTask.template_params,
CampaignTarget's equivalent), so a rule can use a params template too.

Revision ID: c9d0e1f2a3b4
Revises: b5c6d7e8f9a0
Create Date: 2026-08-25 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("follow_up_rules", sa.Column("template_params", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("follow_up_rules", "template_params")
