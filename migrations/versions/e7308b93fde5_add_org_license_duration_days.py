"""add org license_duration_days

Remembers the number of days a license was last issued/renewed for, so a
one-click renew (no form) can reuse that duration instead of a hardcoded
default — see routers/billing.py's renew_license.

Revision ID: e7308b93fde5
Revises: 1a30b485bdee
Create Date: 2026-09-16 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7308b93fde5'
down_revision: Union[str, None] = '1a30b485bdee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('license_duration_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'license_duration_days')
