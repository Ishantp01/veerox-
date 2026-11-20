"""add chat_reads (unified Chat module unread markers)

Revision ID: a1c3e5f7b9d1
Revises: e6c4f5a7b8d9
Create Date: 2026-11-19 00:00:00.000000

Additive only: one new table, no changes to conversations/messages.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a1c3e5f7b9d1"
down_revision: Union[str, None] = "e6c4f5a7b8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_reads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("account_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "last_read_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("account_user_id", "user_id", name="uq_chat_reads_account_user_user"),
    )
    op.create_index("ix_chat_reads_user_id", "chat_reads", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_reads_user_id", table_name="chat_reads")
    op.drop_table("chat_reads")
