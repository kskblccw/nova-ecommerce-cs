"""消息幂等与会话列表聚合查询集成测试。"""

import uuid

import pytest
from sqlalchemy import delete, func, select

from ecommerce_cs.core.errors import ConflictError
from ecommerce_cs.core.security import hash_phone, mask_phone
from ecommerce_cs.db.models import Conversation, ConversationSummary, Message, User
from ecommerce_cs.db.repositories import chat as chat_repo
from ecommerce_cs.db.session import SessionLocal

pytestmark = pytest.mark.integration


async def _seed_user(db) -> User:
    suffix = f"{uuid.uuid4().int % 10**8:08d}"
    phone = f"199{suffix}"
    user = User(
        name="幂等测试用户",
        phone_hash=hash_phone(phone),
        phone_masked=mask_phone(phone),
        password_hash="x",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_client_message_id_is_idempotent(db_ready):
    async with SessionLocal() as db:
        user = await _seed_user(db)
        conversation = await chat_repo.create_conversation(db, user_id=user.id)
        client_id = f"client-{uuid.uuid4().hex}"

    try:
        async with SessionLocal() as db:
            first = await chat_repo.add_message(
                db,
                conversation_id=conversation.id,
                role="user",
                content="第一次发送",
                client_message_id=client_id,
            )
            second = await chat_repo.add_message(
                db,
                conversation_id=conversation.id,
                role="user",
                content="重试发送",
                client_message_id=client_id,
            )
            total = int(
                await db.scalar(
                    select(func.count(Message.id)).where(
                        Message.conversation_id == conversation.id
                    )
                )
                or 0
            )

        assert first.id == second.id
        assert second.content == "第一次发送"
        assert total == 1
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
            await db.execute(
                delete(ConversationSummary).where(
                    ConversationSummary.conversation_id == conversation.id
                )
            )
            await db.execute(delete(Conversation).where(Conversation.id == conversation.id))
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()


@pytest.mark.asyncio
async def test_client_message_id_cannot_cross_conversation(db_ready):
    async with SessionLocal() as db:
        user = await _seed_user(db)
        first_conversation = await chat_repo.create_conversation(db, user_id=user.id)
        second_conversation = await chat_repo.create_conversation(db, user_id=user.id)
        client_id = f"client-{uuid.uuid4().hex}"

    try:
        async with SessionLocal() as db:
            await chat_repo.add_message(
                db,
                conversation_id=first_conversation.id,
                role="user",
                content="会话一",
                client_message_id=client_id,
            )
            with pytest.raises(ConflictError):
                await chat_repo.add_message(
                    db,
                    conversation_id=second_conversation.id,
                    role="user",
                    content="会话二",
                    client_message_id=client_id,
                )
    finally:
        conversation_ids = [first_conversation.id, second_conversation.id]
        async with SessionLocal() as db:
            await db.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
            await db.execute(
                delete(ConversationSummary).where(
                    ConversationSummary.conversation_id.in_(conversation_ids)
                )
            )
            await db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()


@pytest.mark.asyncio
async def test_list_messages_page_uses_cursor(db_ready):
    async with SessionLocal() as db:
        user = await _seed_user(db)
        conversation = await chat_repo.create_conversation(db, user_id=user.id)
        for index in range(5):
            await chat_repo.add_message(
                db,
                conversation_id=conversation.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"消息 {index}",
            )

    try:
        async with SessionLocal() as db:
            first, has_more, cursor = await chat_repo.list_messages_page(
                db, conversation.id, limit=2
            )
            assert [message.content for message in first] == ["消息 3", "消息 4"]
            assert has_more is True and cursor is not None

            second, has_more_second, cursor_second = await chat_repo.list_messages_page(
                db, conversation.id, limit=2, before=cursor
            )
            assert [message.content for message in second] == ["消息 1", "消息 2"]
            assert has_more_second is True and cursor_second is not None

            third, has_more_third, _ = await chat_repo.list_messages_page(
                db, conversation.id, limit=2, before=cursor_second
            )
            assert [message.content for message in third] == ["消息 0"]
            assert has_more_third is False
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
            await db.execute(
                delete(ConversationSummary).where(
                    ConversationSummary.conversation_id == conversation.id
                )
            )
            await db.execute(delete(Conversation).where(Conversation.id == conversation.id))
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()


@pytest.mark.asyncio
async def test_conversation_overviews_include_count_and_preview(db_ready):
    async with SessionLocal() as db:
        user = await _seed_user(db)
        first = await chat_repo.create_conversation(db, user_id=user.id, title="会话一")
        second = await chat_repo.create_conversation(db, user_id=user.id, title="会话二")
        await chat_repo.add_message(
            db, conversation_id=first.id, role="user", content="第一条消息"
        )
        await chat_repo.add_message(
            db, conversation_id=first.id, role="assistant", content="最新一条回复"
        )
        await chat_repo.add_message(
            db, conversation_id=second.id, role="user", content="第二个会话的消息"
        )

    try:
        async with SessionLocal() as db:
            overviews = {
                conversation.id: (count, preview)
                for conversation, count, preview in await chat_repo.list_conversation_overviews(
                    db, user.id
                )
            }
        assert overviews[first.id] == (2, "最新一条回复")
        assert overviews[second.id] == (1, "第二个会话的消息")
    finally:
        conversation_ids = [first.id, second.id]
        async with SessionLocal() as db:
            await db.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
            await db.execute(
                delete(ConversationSummary).where(
                    ConversationSummary.conversation_id.in_(conversation_ids)
                )
            )
            await db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()
