"""add lead status presets table

Org-scoped custom pipeline stages (see db/models/lead_status_preset.py) that
sit alongside the built-in new/contacted/qualified/converted/lost statuses —
picked (or created) from the lead status dropdown. Purely additive: Lead.status
stays the same unconstrained String(20) column, just written to with a
preset's name instead of one of the built-ins.

Revision ID: 395af78986a5
Revises: dae24643f256
Create Date: 2026-09-18 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '395af78986a5'
down_revision: Union[str, None] = 'dae24643f256'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'lead_status_presets',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'name', name='uq_lead_status_presets_org_id_name'),
    )
    op.create_index('ix_lead_status_presets_org_id', 'lead_status_presets', ['org_id'])


def downgrade() -> None:
    op.drop_index('ix_lead_status_presets_org_id', table_name='lead_status_presets')
    op.drop_table('lead_status_presets')
