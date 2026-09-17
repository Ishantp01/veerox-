"""add template header/button params to follow-up rules/tasks and campaigns

Follow-up rules/tasks previously only carried a template's body params
(template_params) — no way to configure a media (IMAGE/VIDEO/DOCUMENT)
header or dynamic URL/COPY_CODE buttons, so a template with either would
silently fail to deliver (Meta rejects the send outright: "Format mismatch,
expected IMAGE, received UNKNOWN"). Adds template_header_params (same
convention as CallCampaign.template_header_params) and
template_button_params (same shape as OutboundWhatsappIn's, see
schemas/whatsapp_common.TemplateButtonSendParam) to follow_up_rules and
follow_up_tasks.

Also adds template_button_params to call_campaigns, which already had
template_header_params but never gained button support.

Revision ID: 8b323b795c46
Revises: 3c74f9c0e54a
Create Date: 2026-09-17 16:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8b323b795c46'
down_revision: Union[str, None] = '3c74f9c0e54a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('follow_up_rules', sa.Column('template_header_params', sa.JSON(), nullable=True))
    op.add_column('follow_up_rules', sa.Column('template_button_params', sa.JSON(), nullable=True))
    op.add_column('follow_up_tasks', sa.Column('template_header_params', sa.JSON(), nullable=True))
    op.add_column('follow_up_tasks', sa.Column('template_button_params', sa.JSON(), nullable=True))
    op.add_column('call_campaigns', sa.Column('template_button_params', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('call_campaigns', 'template_button_params')
    op.drop_column('follow_up_tasks', 'template_button_params')
    op.drop_column('follow_up_tasks', 'template_header_params')
    op.drop_column('follow_up_rules', 'template_button_params')
    op.drop_column('follow_up_rules', 'template_header_params')
