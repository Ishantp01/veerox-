"""add org free_plan_claimed_at

Revision ID: d4e5f6a7b8c9
Revises: f7e8a9b0c1d2
Create Date: 2026-08-31 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'f7e8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'orgs',
        sa.Column('free_plan_claimed_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('orgs', 'free_plan_claimed_at')
