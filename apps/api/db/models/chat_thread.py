"""One chat thread per customer, parent of all their conversations.

Every WhatsApp session and every voice call is still its own ``conversations``
row (calls carry a recording, a summary and a webhook id that the pipelines key
on), but each of them now points at the customer's single ``ChatThread`` through
``conversations.chat_thread_id``. So "one conversation per lead" holds in the
database: a lead has exactly one thread, and every past and future interaction
attaches to it.

New conversations are attached automatically by the ``before_flush`` hook below,
so none of the code that creates conversations (WhatsApp, voice, AI tools, the
dashboard) needs to know about threads.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, event, func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Mapped, Session, mapped_column

from apps.api.db.base import Base
from apps.api.db.models.conversation import Conversation


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_chat_threads_org_user"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def _thread_id_for(session: Session, org_id: UUID, user_id: UUID) -> UUID:
    """The customer's thread id, creating the thread on first use. The insert is
    ``ON CONFLICT DO NOTHING`` so two conversations starting at once (say a call
    and a WhatsApp message) can't both try to create it."""
    table = ChatThread.__table__
    lookup = select(table.c.id).where(table.c.org_id == org_id, table.c.user_id == user_id)
    found = session.execute(lookup).scalar_one_or_none()
    if found is not None:
        return found
    insert = (sqlite if session.get_bind().dialect.name == "sqlite" else postgresql).insert
    session.execute(
        insert(table)
        .values(id=uuid4(), org_id=org_id, user_id=user_id)
        .on_conflict_do_nothing(index_elements=["org_id", "user_id"])
    )
    return session.execute(lookup).scalar_one()


@event.listens_for(Session, "before_flush")
def _attach_new_conversations_to_thread(session: Session, flush_context, instances) -> None:  # noqa: ANN001
    pending = [
        obj
        for obj in session.new
        if isinstance(obj, Conversation)
        and obj.chat_thread_id is None
        and obj.org_id is not None
        and obj.user_id is not None
    ]
    if not pending:
        return
    with session.no_autoflush:
        cache: dict[tuple[UUID, UUID], UUID] = {}
        for conversation in pending:
            key = (conversation.org_id, conversation.user_id)
            if key not in cache:
                cache[key] = _thread_id_for(session, *key)
            conversation.chat_thread_id = cache[key]
