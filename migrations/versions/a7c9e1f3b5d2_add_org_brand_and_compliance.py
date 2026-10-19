"""add org voice brand and compliance policy

Revision ID: a7c9e1f3b5d2
Revises: f3b5d7e9a1c2
Create Date: 2026-10-20 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c9e1f3b5d2'
down_revision: Union[str, None] = 'f3b5d7e9a1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive and nullable: NULL keeps every existing org on its current behaviour
    # (legacy brand, record every call with no notice). Rolling back loses no customer data.
    op.add_column('orgs', sa.Column('voice_brand', sa.String(length=80), nullable=True))
    op.add_column('orgs', sa.Column('voice_compliance', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'voice_compliance')
    op.drop_column('orgs', 'voice_brand')
