"""platform-wide scenario templates

Revision ID: d0f6b4c8e2a5
Revises: c9e5a3b7d1f4
Create Date: 2026-10-16 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd0f6b4c8e2a5'
down_revision: Union[str, None] = 'c9e5a3b7d1f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive: one new table, nothing existing is touched.
    op.create_table(
        'conversation_scenario_templates',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('scenario_id', sa.String(length=31), nullable=False),
        sa.Column('definition', sa.JSON(), nullable=False),
        sa.Column('updated_by', sa.String(length=120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scenario_id'),
    )


def downgrade() -> None:
    op.drop_table('conversation_scenario_templates')
