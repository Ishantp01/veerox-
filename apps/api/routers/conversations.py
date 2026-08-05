from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from apps.api.db.models import Conversation, Message
from apps.api.deps import DbDep, verify_admin_or_session
from apps.api.schemas.conversation import ConversationOut, MessageOut

router = APIRouter(
    prefix="/conversations", tags=["conversations"], dependencies=[Depends(verify_admin_or_session)]
)


@router.get("/{user_id}", response_model=list[ConversationOut])
async def list_conversations(
    user_id: UUID,
    db: DbDep,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


@router.get("/{user_id}/{conversation_id}/messages", response_model=list[MessageOut])
async def get_transcript(
    user_id: UUID,
    conversation_id: UUID,
    db: DbDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.user_id == user_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
