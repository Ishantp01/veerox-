"""add call_campaigns.template_header_params

Campaigns can now fill a WhatsApp template's HEADER placeholder (a {{1}}
text value, or a media URL/asset name for an IMAGE/VIDEO/DOCUMENT header),
same as the single-send outbound WhatsApp form already supported — see
db/models/call_campaign.py.

Revision ID: d3f5a8b1c2e4
Revises: ec0f3a7945ab
Create Date: 2026-09-15 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3f5a8b1c2e4'
down_revision: Union[str, None] = 'ec0f3a7945ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'call_campaigns', sa.Column('template_header_params', sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('call_campaigns', 'template_header_params')
