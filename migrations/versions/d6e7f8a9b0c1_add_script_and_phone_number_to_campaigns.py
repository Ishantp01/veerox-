"""add script_id and phone_number_id to call_campaigns

Lets a campaign optionally pin a specific voice script (scripts.id) and/or
a specific dedicated number (org_phone_numbers.id) to call from, selected
via dropdowns at campaign-creation time. Both nullable — NULL keeps prior
behavior (org default script, auto-rotating numbers). ON DELETE SET NULL so
removing a script/number a campaign referenced never blocks the delete.

Revision ID: d6e7f8a9b0c1
Revises: e7f8a9b0c1d2
Create Date: 2026-09-04 13:15:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('call_campaigns', sa.Column('script_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_call_campaigns_script_id', 'call_campaigns', 'scripts', ['script_id'], ['id'], ondelete='SET NULL'
    )
    op.add_column('call_campaigns', sa.Column('phone_number_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_call_campaigns_phone_number_id',
        'call_campaigns',
        'org_phone_numbers',
        ['phone_number_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_call_campaigns_phone_number_id', 'call_campaigns', type_='foreignkey')
    op.drop_column('call_campaigns', 'phone_number_id')
    op.drop_constraint('fk_call_campaigns_script_id', 'call_campaigns', type_='foreignkey')
    op.drop_column('call_campaigns', 'script_id')
