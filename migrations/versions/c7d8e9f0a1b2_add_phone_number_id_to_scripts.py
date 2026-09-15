"""add phone_number_id to scripts

Purely a settings-page label pairing a script with one of the org's
dedicated numbers ("this script sends from this number") — doesn't change
which number a send actually uses (still CallCampaign.whatsapp_number_id or
the org's default WhatsApp number). See db/models/script.py.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-09-15 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'b6c7d8e9f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'scripts', sa.Column('phone_number_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        'fk_scripts_phone_number_id_org_phone_numbers',
        'scripts',
        'org_phone_numbers',
        ['phone_number_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_scripts_phone_number_id_org_phone_numbers', 'scripts', type_='foreignkey'
    )
    op.drop_column('scripts', 'phone_number_id')
