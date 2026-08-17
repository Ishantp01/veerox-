"""add template fields to follow_up_tasks

Appointment reminders (see apps/api/core/tools.py's book_appointment) are
sent via a Meta pre-approved WhatsApp template rather than free-form text,
since a reminder must reach the recipient even outside the 24h
customer-service session window that free-form sends require. FollowUpTask
gains an optional template name + ordered body params; when set, the
dispatcher (workers/follow_up_dispatcher.py) sends via that template instead
of the rule/lead free-text path.

Revision ID: b3f8a1d2c4e6
Revises: e7a1c9b2d4f3
Create Date: 2026-08-17 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f8a1d2c4e6'
down_revision: Union[str, None] = 'e7a1c9b2d4f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("follow_up_tasks", sa.Column("template_name", sa.String(length=255), nullable=True))
    op.add_column("follow_up_tasks", sa.Column("template_params", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("follow_up_tasks", "template_params")
    op.drop_column("follow_up_tasks", "template_name")
