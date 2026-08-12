"""add twilio_phone_number to orgs

Mutually exclusive with the existing plivo_phone_number: an org's dedicated
calling number lands in whichever column matches the provider that actually
owns it (see channels/voice/number_provider.py::detect_provider), so
channels/voice/failover.py can dial from — and fail over between — the
correct provider for that org's own number.

Revision ID: a1b2c3d4e5f6
Revises: e5f6a7b8c9d0
Create Date: 2026-08-10 14:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('twilio_phone_number', sa.String(length=32), nullable=True))
    op.create_unique_constraint(
        'uq_orgs_twilio_phone_number', 'orgs', ['twilio_phone_number']
    )


def downgrade() -> None:
    op.drop_constraint('uq_orgs_twilio_phone_number', 'orgs', type_='unique')
    op.drop_column('orgs', 'twilio_phone_number')
