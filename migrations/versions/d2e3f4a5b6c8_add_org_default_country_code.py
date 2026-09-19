"""add orgs.default_country_code

Dialing prefix chosen when an org is created; numbers entered without an
international prefix get it prepended (core/phone.py). Existing orgs are
backfilled to +91 via the server default.

Revision ID: d2e3f4a5b6c8
Revises: c1d2e3f4a5b7
Create Date: 2026-09-19 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2e3f4a5b6c8'
down_revision: Union[str, None] = 'c1d2e3f4a5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'orgs',
        sa.Column('default_country_code', sa.String(length=6), nullable=False, server_default='+91'),
    )


def downgrade() -> None:
    op.drop_column('orgs', 'default_country_code')
