"""add campaign scheduling and whatsapp template fields

Two independent, additive changes bundled together since both stem from the
same fix (uploads no longer auto-start outreach, and WhatsApp sends can use
a pre-approved template instead of always free text):

  - call_campaigns: new "draft"/"scheduled" statuses (server_default flips
    from "running" to "draft" — existing rows are untouched, only future
    inserts that omit status pick up the new default), scheduled_start_at,
    and template_name/template_language/template_params/custom_message for
    WhatsApp campaigns.
  - follow_up_rules: template_name/template_language, and message_template
    widened to nullable since a rule may now be template-only.

Revision ID: f1a2b3c4d5e6
Revises: e7b3a5c9f2d4
Create Date: 2026-08-21 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e7b3a5c9f2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'call_campaigns', sa.Column('scheduled_start_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('call_campaigns', sa.Column('template_name', sa.String(255), nullable=True))
    op.add_column('call_campaigns', sa.Column('template_language', sa.String(10), nullable=True))
    op.add_column('call_campaigns', sa.Column('template_params', sa.JSON(), nullable=True))
    op.add_column('call_campaigns', sa.Column('custom_message', sa.Text(), nullable=True))
    op.alter_column('call_campaigns', 'status', server_default='draft')

    op.add_column('follow_up_rules', sa.Column('template_name', sa.String(255), nullable=True))
    op.add_column('follow_up_rules', sa.Column('template_language', sa.String(10), nullable=True))
    op.alter_column('follow_up_rules', 'message_template', existing_type=sa.Text(), nullable=True)
    op.add_column('follow_up_tasks', sa.Column('template_language', sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column('follow_up_tasks', 'template_language')
    op.alter_column('follow_up_rules', 'message_template', existing_type=sa.Text(), nullable=False)
    op.drop_column('follow_up_rules', 'template_language')
    op.drop_column('follow_up_rules', 'template_name')

    op.alter_column('call_campaigns', 'status', server_default='running')
    op.drop_column('call_campaigns', 'custom_message')
    op.drop_column('call_campaigns', 'template_params')
    op.drop_column('call_campaigns', 'template_language')
    op.drop_column('call_campaigns', 'template_name')
    op.drop_column('call_campaigns', 'scheduled_start_at')
