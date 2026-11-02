"""会话路由：列表、创建、重命名、删除与消息历史。"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.api.deps import get_current_user
from ecommerce_cs.core.errors import ForbiddenError, NotFoundError
from ecommerce_cs.db.models import User
from ecommerce_cs.db.repositories import chat as chat_repo
from ecommerce_cs.db.session import get_db
from ecommerce_cs.schemas.chat import (
    ConversationCreate,
    ConversationOut,
    ConversationUpdate,
    MessageOut,
    MessagePage,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _require_conversation(db: AsyncSession, conversation_id: str, user: User):
    conversation = await chat_repo.get_conversation(db, conversation_id, user.id)
    if not conversation:
        raise NotFoundError("会话不存在")
    return conversation


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[ConversationOut]:
    conversations = await chat_repo.list_conversation_overviews(db, user.id)
    return [
        ConversationOut(
            id=conversation.id,
            title=conversation.title,
            created_at=conversation.created_at,
            last_message_at=conversation.last_message_at,
            message_count=count,
            preview=preview,
        )
        for conversation, count, preview in conversations
    ]


@router.post("", response_model=ConversationOut, status_code=201)
async def create_conversation(
    payload: ConversationCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    conversation = await chat_repo.create_conversation(
        db, user_id=user.id, title=payload.title
    )
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        last_message_at=conversation.last_message_at,
        message_count=0,
    )


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    conversation = await _require_conversation(db, conversation_id, user)
    await chat_repo.update_conversation_title(db, conversation.id, payload.title)
    conversation.title = payload.title
    count = await chat_repo.count_messages(db, conversation.id)
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        last_message_at=conversation.last_message_at,
        message_count=count,
    )


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    conversation = await _require_conversation(db, conversation_id, user)
    await chat_repo.delete_conversation(db, conversation.id)
    return {"deleted": conversation.id}


@router.get("/{conversation_id}/messages", response_model=MessagePage)
async def list_messages(
    conversation_id: str,
    limit: int = Query(default=30, ge=1, le=100),
    before: datetime | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessagePage:
    conversation = await chat_repo.get_conversation(db, conversation_id, user.id)
    if not conversation:
        raise ForbiddenError("无权访问该会话")
    items, has_more, next_cursor = await chat_repo.list_messages_page(
        db, conversation.id, limit=limit, before=before
    )
    return MessagePage(
        items=[MessageOut.model_validate(message) for message in items],
        has_more=has_more,
        next_cursor=next_cursor,
    )
