"""add platform_settings singleton table

Platform-wide, admin-editable settings that aren't tied to any one Org or
Plan: the help-desk chatbot's system script (apps/api/core/prompts.py's
HELP_DESK_SYSTEM_PROMPT is the fallback default) and the social-media links
shown to every client org. A single row (id=1) is seeded so the app can
always assume the row exists.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-08-08 00:20:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

platform_settings = sa.table(
    "platform_settings",
    sa.column("id", sa.Integer),
    sa.column("help_desk_script", sa.Text),
    sa.column("social_links", sa.JSON),
)


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("help_desk_script", sa.Text(), nullable=True),
        sa.Column("social_links", sa.JSON(), nullable=False),
    )
    op.bulk_insert(platform_settings, [{"id": 1, "help_desk_script": None, "social_links": {}}])


def downgrade() -> None:
    op.drop_table("platform_settings")
