"""add social_links to orgs

Org-wide social/contact links (website, Instagram, Facebook, etc.), set once
via PUT /admin/settings/social-links and then surfaced to the WhatsApp/voice
agent automatically (see core/org_social_links.py::social_links_prompt_block)
instead of the org needing to paste them into its script text. NULL/empty for
every existing org.

Revision ID: f18ef077b740
Revises: 1c2d3e4f5a6b
Create Date: 2026-09-17 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f18ef077b740'
down_revision: Union[str, None] = '1c2d3e4f5a6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('social_links', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'social_links')
