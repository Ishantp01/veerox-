"""add org voice profile

Revision ID: f3b5d7e9a1c2
Revises: e2a4c6d8f0b1
Create Date: 2026-10-19 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3b5d7e9a1c2'
down_revision: Union[str, None] = 'e2a4c6d8f0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive and nullable: existing orgs keep the platform voice (NULL).
    op.add_column('orgs', sa.Column('voice_profile', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'voice_profile')
