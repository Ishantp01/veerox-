"""add position to org_phone_numbers

Outbound calls now round-robin across every number an org has per provider
(see channels/voice/org_numbers.py::get_rotating_numbers) instead of always
dialing the single is_default one. Rotation order needs something more
reliable than created_at, since several rows written by one
replace_org_phone_numbers call (a single PUT from the settings page) can
land in the same transaction and get an identical timestamp. `position` is
that provider's 0-based index in submission order, backfilled here to 0 for
every existing row (harmless: an org with only one number per provider
rotates trivially to that same number regardless of position, and any org
with several will get real position values the next time its numbers are
saved).

Revision ID: a3b4c5d6e7f8
Revises: c3d4e5f6a7b9
Create Date: 2026-09-04 11:32:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'c3d4e5f6a7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'org_phone_numbers',
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('org_phone_numbers', 'position')
