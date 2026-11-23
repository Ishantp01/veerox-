"""add waba_id to org_phone_numbers and whatsapp_templates

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c0e1
Create Date: 2026-11-24 00:00:00.000000

Additive: two nullable columns, nothing moved or deleted. NULL means "the org's default WABA" (the one saved in its Meta
credentials), which is what every existing number and template effectively is, so current behaviour is unchanged until an
org sets a WABA on a number. Rolling back drops the two columns.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3e5a7b9d1f2"
down_revision: Union[str, None] = "b2d4f6a8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("org_phone_numbers", sa.Column("waba_id", sa.String(length=32), nullable=True))
    op.add_column("whatsapp_templates", sa.Column("waba_id", sa.String(length=32), nullable=True))
    op.create_index("ix_whatsapp_templates_waba_id", "whatsapp_templates", ["waba_id"])


def downgrade() -> None:
    op.drop_index("ix_whatsapp_templates_waba_id", table_name="whatsapp_templates")
    op.drop_column("whatsapp_templates", "waba_id")
    op.drop_column("org_phone_numbers", "waba_id")
