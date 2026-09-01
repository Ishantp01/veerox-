"""add preferred_voice_provider to orgs

Explicit override of failover.py's automatic Plivo-first/Twilio-fallback
ordering. "plivo", "twilio", or NULL (automatic — the existing default
behavior, unaffected for every org until this is set).

Revision ID: b2c3d4e5f6a7
Revises: d4e5f6a7b8c9
Create Date: 2026-09-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('preferred_voice_provider', sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'preferred_voice_provider')
