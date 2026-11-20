from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class ChatRead(Base):
    """When one staff member last opened one customer's unified chat.

    The Chat module's unread badge is "inbound messages newer than this".
    Keyed per (account_user, customer) rather than per conversation because the
    Chat module shows every conversation row of a customer as a single thread.
    Purely additive — nothing in the WhatsApp/voice pipelines reads or writes it.
    """

    __tablename__ = "chat_reads"
    __table_args__ = (
        UniqueConstraint("account_user_id", "user_id", name="uq_chat_reads_account_user_user"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    account_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account_users.id", ondelete="CASCADE"), nullable=False
    )
    # The customer (users.id) whose thread was read.
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
