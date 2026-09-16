"""add openai_api_key_encrypted to orgs

Lets an org bring its own OpenAI key (Fernet-encrypted at rest, see
apps/api/core/crypto.py) instead of billing against the platform's shared
key. NULL for every existing org — unaffected until set via
PUT /admin/settings/openai-key.

Revision ID: f010beb97c28
Revises: c7d8e9f0a1b2
Create Date: 2026-09-16 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f010beb97c28'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('openai_api_key_encrypted', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'openai_api_key_encrypted')
