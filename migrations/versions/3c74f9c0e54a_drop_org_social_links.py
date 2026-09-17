"""drop social_links from orgs

Superseded by the platform-wide PlatformSettings.social_links (Settings ->
Social Links, superuser-only) — a per-org duplicate of that same concept
caused confusion (two "Social Links" settings screens saving to two
different places). The WhatsApp/voice agent's prompt block now reads the
platform-wide table instead (core/org_social_links.py::social_links_prompt_block).

Revision ID: 3c74f9c0e54a
Revises: f18ef077b740
Create Date: 2026-09-17 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '3c74f9c0e54a'
down_revision: Union[str, None] = 'f18ef077b740'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('orgs', 'social_links')


def downgrade() -> None:
    op.add_column('orgs', sa.Column('social_links', postgresql.JSONB(), nullable=True))
