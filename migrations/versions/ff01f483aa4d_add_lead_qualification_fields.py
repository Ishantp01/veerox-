"""add lead qualification fields

Revision ID: ff01f483aa4d
Revises: 655bab7b4adb
Create Date: 2026-07-27 00:10:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ff01f483aa4d'
down_revision: Union[str, None] = '655bab7b4adb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'leads',
        sa.Column('qualification_status', sa.String(length=20), nullable=False, server_default='unqualified'),
    )
    op.add_column('leads', sa.Column('qualification_score', sa.Integer(), nullable=True))
    op.add_column('leads', sa.Column('qualification_notes', sa.Text(), nullable=True))
    op.add_column('leads', sa.Column('qualified_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_leads_qualification_status', 'leads', ['qualification_status'])


def downgrade() -> None:
    op.drop_index('ix_leads_qualification_status', table_name='leads')
    op.drop_column('leads', 'qualified_at')
    op.drop_column('leads', 'qualification_notes')
    op.drop_column('leads', 'qualification_score')
    op.drop_column('leads', 'qualification_status')
