"""add whatsapp templates

Revision ID: b2e6f9a1c3d7
Revises: d48d309514bf
Create Date: 2026-07-28 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b2e6f9a1c3d7'
down_revision: Union[str, None] = 'd48d309514bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'whatsapp_templates',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('language', sa.String(length=10), server_default='en_US', nullable=False),
        sa.Column('category', sa.String(length=30), nullable=True),
        sa.Column('param_labels', sa.JSON(), nullable=False),
        sa.Column('body_preview', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_whatsapp_templates_org_id', 'whatsapp_templates', ['org_id'])


def downgrade() -> None:
    op.drop_index('ix_whatsapp_templates_org_id', table_name='whatsapp_templates')
    op.drop_table('whatsapp_templates')
