"""add plan_started_at to orgs

Marks when an org's *current* plan took effect, so usage aggregation
(apps/api/core/usage.py) can start counting from that point instead of
always from the calendar month's start — otherwise usage already
accumulated under a previous (or lower) plan this month would count
against a freshly-upgraded plan's limits.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-01 00:10:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('plan_started_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'plan_started_at')
