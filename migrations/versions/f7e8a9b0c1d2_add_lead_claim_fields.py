"""add lead claim fields

Revision ID: f7e8a9b0c1d2
Revises: e1f2a3b4c5d6
Create Date: 2026-08-29 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f7e8a9b0c1d2'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'leads',
        sa.Column('claimed_by_account_user_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        'leads',
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_leads_claimed_by_account_user_id',
        'leads',
        'account_users',
        ['claimed_by_account_user_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_leads_claimed_by_account_user_id', 'leads', type_='foreignkey')
    op.drop_column('leads', 'claimed_at')
    op.drop_column('leads', 'claimed_by_account_user_id')
