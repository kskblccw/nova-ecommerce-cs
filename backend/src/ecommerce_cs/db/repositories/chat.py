"""会话、消息、摘要与用户画像的仓储。"""

from datetime import UTC, datetime

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.core.errors import ConflictError
from ecommerce_cs.db.models import (
    Conversation,
    ConversationSummary,
    Message,
    UserProfile,
)


async def list_conversations(db: AsyncSession, user_id: int) -> list[Conversation]:
    result = await db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(desc(Conversation.last_message_at), desc(Conversation.created_at))
    )
    return list(result.all())


async def list_conversation_overviews(
    db: AsyncSession, user_id: int
) -> list[tuple[Conversation, int, str]]:
    """列表页一次性取回会话、消息数与预览，避免逐会话 count/select 的 N+1。"""
    conversations = await list_conversations(db, user_id)
    if not conversations:
        return []

    conversation_ids = [conversation.id for conversation in conversations]
    count_rows = await db.execute(
        select(Message.conversation_id, func.count(Message.id))
        .where(Message.conversation_id.in_(conversation_ids))
        .group_by(Message.conversation_id)
    )
    counts = {row[0]: int(row[1]) for row in count_rows.all()}

    row_number = func.row_number().over(
        partition_by=Message.conversation_id,
        order_by=desc(Message.created_at),
    )
    latest = (
        select(
            Message.conversation_id,
            Message.content,
            row_number.label("row_number"),
        )
        .where(Message.conversation_id.in_(conversation_ids))
        .subquery()
    )
    last_rows = await db.execute(
        select(latest.c.conversation_id, latest.c.content).where(
            latest.c.row_number == 1
        )
    )
    last_messages = {row[0]: row[1] for row in last_rows.all()}

    return [
        (
            conversation,
            counts.get(conversation.id, 0),
            (last_messages.get(conversation.id) or "")[:60],
        )
        for conversation in conversations
    ]


async def get_conversation(
    db: AsyncSession, conversation_id: str, user_id: int
) -> Conversation | None:
    return await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )


async def create_conversation(
    db: AsyncSession, *, user_id: int, title: str = "新会话"
) -> Conversation:
    conversation = Conversation(user_id=user_id, title=title)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def update_conversation_title(
    db: AsyncSession, conversation_id: str, title: str
) -> None:
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(title=title)
    )
    await db.commit()


async def touch_conversation(db: AsyncSession, conversation_id: str) -> None:
    now = datetime.now(UTC)
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(last_message_at=now, updated_at=now)
    )
    await db.commit()


async def delete_conversation(db: AsyncSession, conversation_id: str) -> None:
    await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
    await db.commit()


async def list_messages(db: AsyncSession, conversation_id: str) -> list[Message]:
    result = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return list(result.all())


async def list_messages_page(
    db: AsyncSession,
    conversation_id: str,
    *,
    limit: int = 30,
    before: datetime | None = None,
) -> tuple[list[Message], bool, datetime | None]:
    """游标分页：按 created_at 倒序取一页，再转成升序返回给前端。"""
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(desc(Message.created_at), desc(Message.id))
        .limit(limit + 1)
    )
    if before is not None:
        statement = statement.where(Message.created_at < before)

    rows = list((await db.scalars(statement)).all())
    has_more = len(rows) > limit
    items = list(reversed(rows[:limit]))
    next_cursor = items[0].created_at if has_more and items else None
    return items, has_more, next_cursor


async def get_message(db: AsyncSession, message_id: str) -> Message | None:
    return await db.scalar(select(Message).where(Message.id == message_id))


async def get_message_by_client_id(
    db: AsyncSession, client_message_id: str
) -> Message | None:
    if not client_message_id:
        return None
    return await db.scalar(
        select(Message).where(Message.client_message_id == client_message_id)
    )


async def get_last_message(db: AsyncSession, conversation_id: str) -> Message | None:
    return await db.scalar(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(desc(Message.created_at))
        .limit(1)
    )


async def add_message(
    db: AsyncSession,
    *,
    conversation_id: str,
    role: str,
    content: str,
    parts: list | None = None,
    client_message_id: str | None = None,
) -> Message:
    if client_message_id:
        existing = await get_message_by_client_id(db, client_message_id)
        if existing:
            if existing.conversation_id != conversation_id:
                raise ConflictError("客户端消息 ID 已被其他会话使用")
            return existing

    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        parts=parts or [],
        client_message_id=client_message_id,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


async def count_messages(db: AsyncSession, conversation_id: str) -> int:
    return int(
        await db.scalar(
            select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
        )
        or 0
    )


async def get_summary(db: AsyncSession, conversation_id: str) -> str:
    row = await get_summary_record(db, conversation_id)
    return row.summary if row else ""


async def get_summary_record(
    db: AsyncSession, conversation_id: str
) -> ConversationSummary | None:
    return await db.scalar(
        select(ConversationSummary).where(
            ConversationSummary.conversation_id == conversation_id
        )
    )


async def upsert_summary(
    db: AsyncSession, conversation_id: str, summary: str, token_count: int
) -> None:
    row = await db.scalar(
        select(ConversationSummary).where(
            ConversationSummary.conversation_id == conversation_id
        )
    )
    if row:
        row.summary = summary
        row.token_count = token_count
    else:
        db.add(
            ConversationSummary(
                conversation_id=conversation_id,
                summary=summary,
                token_count=token_count,
            )
        )
    await db.commit()


async def get_profile(db: AsyncSession, user_id: int) -> UserProfile | None:
    return await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))


async def upsert_profile(
    db: AsyncSession,
    *,
    user_id: int,
    preferences: list,
    facts: dict,
    weight: float,
) -> None:
    row = await get_profile(db, user_id)
    if row:
        row.preferences = preferences
        row.facts = facts
        row.weight = weight
        row.last_active_at = datetime.now(UTC)
    else:
        db.add(
            UserProfile(
                user_id=user_id,
                preferences=preferences,
                facts=facts,
                weight=weight,
            )
        )
    await db.commit()
