"""add tags to campaign_targets, appointments, conversations

Same free-form JSON-array convention as the existing Lead.tags column —
lets a campaign contact be tagged on CSV import (carried onto the Lead a
qualified target becomes), and appointments/conversations be tagged from
their detail views.

Revision ID: c1d2e3f4a5b7
Revises: b8c9d0e1f2a3
Create Date: 2026-09-19 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d2e3f4a5b7'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('campaign_targets', sa.Column('tags', sa.JSON(), nullable=True))
    op.add_column('appointments', sa.Column('tags', sa.JSON(), nullable=True))
    op.add_column('conversations', sa.Column('tags', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('conversations', 'tags')
    op.drop_column('appointments', 'tags')
    op.drop_column('campaign_targets', 'tags')
