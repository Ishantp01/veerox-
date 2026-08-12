"""add whatsapp_phone_number_id and plivo_phone_number to orgs

Lets an inbound WhatsApp message or phone call be attributed to the org that
owns the number it arrived on, instead of always falling back to
settings.default_org_id (see channels/whatsapp/adapter.py::process_inbound
and channels/voice/webhook.py::answer).

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-08 00:10:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('whatsapp_phone_number_id', sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        'uq_orgs_whatsapp_phone_number_id', 'orgs', ['whatsapp_phone_number_id']
    )
    op.add_column('orgs', sa.Column('plivo_phone_number', sa.String(length=32), nullable=True))
    op.create_unique_constraint('uq_orgs_plivo_phone_number', 'orgs', ['plivo_phone_number'])


def downgrade() -> None:
    op.drop_constraint('uq_orgs_plivo_phone_number', 'orgs', type_='unique')
    op.drop_column('orgs', 'plivo_phone_number')
    op.drop_constraint('uq_orgs_whatsapp_phone_number_id', 'orgs', type_='unique')
    op.drop_column('orgs', 'whatsapp_phone_number_id')
