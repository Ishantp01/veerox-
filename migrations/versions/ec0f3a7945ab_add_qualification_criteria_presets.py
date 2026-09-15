"""add qualification criteria presets table

Reusable, named qualification-criteria library per org (see
db/models/qualification_criteria_preset.py) — picked from a dropdown when
creating a campaign instead of retyping the bar every time. Purely
additive: CallCampaign.criteria is untouched, still a free-text column
populated from whichever preset (or one-off text) was chosen at creation.

Revision ID: ec0f3a7945ab
Revises: e9e0c7c0cf56
Create Date: 2026-09-15 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'ec0f3a7945ab'
down_revision: Union[str, None] = 'e9e0c7c0cf56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'qualification_criteria_presets',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('criteria_text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_qualification_criteria_presets_org_id', 'qualification_criteria_presets', ['org_id']
    )


def downgrade() -> None:
    op.drop_index('ix_qualification_criteria_presets_org_id', table_name='qualification_criteria_presets')
    op.drop_table('qualification_criteria_presets')
