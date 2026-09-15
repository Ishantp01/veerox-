"""add header/footer/buttons to whatsapp_templates

Extends the local template row to cover the rest of Meta's message
template components (HEADER, FOOTER, BUTTONS) — previously only BODY was
supported end-to-end (apps/api/channels/whatsapp/client.py's
create_template).

Revision ID: e9e0c7c0cf56
Revises: c1d2e3f4a5b6
Create Date: 2026-09-14 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e9e0c7c0cf56'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("whatsapp_templates", sa.Column("header_type", sa.String(length=20), nullable=True))
    op.add_column("whatsapp_templates", sa.Column("header_text", sa.String(length=60), nullable=True))
    op.add_column("whatsapp_templates", sa.Column("header_example", sa.String(length=255), nullable=True))
    op.add_column("whatsapp_templates", sa.Column("footer_text", sa.String(length=60), nullable=True))
    op.add_column(
        "whatsapp_templates",
        sa.Column("buttons", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_templates", "buttons")
    op.drop_column("whatsapp_templates", "footer_text")
    op.drop_column("whatsapp_templates", "header_example")
    op.drop_column("whatsapp_templates", "header_text")
    op.drop_column("whatsapp_templates", "header_type")
