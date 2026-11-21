"""add chat_threads: one parent thread per customer for all their conversations

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5f7b9d1
Create Date: 2026-11-21 00:00:00.000000

Additive: a new table plus a nullable column on conversations. No conversation,
message or call row is moved, merged or deleted. Existing conversations are
attached to a thread by the backfill below; new ones are attached by the app.
Rolling back drops the column and the table and leaves every conversation as is.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b2d4f6a8c0e1"
down_revision: Union[str, None] = "a1c3e5f7b9d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("org_id", "user_id", name="uq_chat_threads_org_user"),
    )
    op.create_index("ix_chat_threads_org_id", "chat_threads", ["org_id"])
    op.create_index("ix_chat_threads_user_id", "chat_threads", ["user_id"])

    op.add_column(
        "conversations",
        sa.Column(
            "chat_thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_conversations_chat_thread_id", "conversations", ["chat_thread_id"])

    # Backfill: one thread per customer that already has conversations, then
    # attach each of their conversations to it.
    op.execute(
        """
        INSERT INTO chat_threads (id, org_id, user_id, created_at)
        SELECT gen_random_uuid(), org_id, user_id, MIN(started_at)
        FROM conversations
        GROUP BY org_id, user_id
        """
    )
    op.execute(
        """
        UPDATE conversations c
        SET chat_thread_id = t.id
        FROM chat_threads t
        WHERE t.org_id = c.org_id AND t.user_id = c.user_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_chat_thread_id", table_name="conversations")
    op.drop_column("conversations", "chat_thread_id")
    op.drop_index("ix_chat_threads_user_id", table_name="chat_threads")
    op.drop_index("ix_chat_threads_org_id", table_name="chat_threads")
    op.drop_table("chat_threads")
