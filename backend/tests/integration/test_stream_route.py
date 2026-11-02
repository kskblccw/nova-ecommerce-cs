"""SSE 路由集成测试：流式响应头、事件输出、心跳、待确认守卫与并发槽位释放。"""

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from ecommerce_cs.api.deps import get_current_user
from ecommerce_cs.core import ratelimit
from ecommerce_cs.core.security import hash_phone, mask_phone
from ecommerce_cs.db.models import Conversation, ConversationSummary, Message, PendingAction, User
from ecommerce_cs.db.repositories import ops as ops_repo
from ecommerce_cs.db.session import SessionLocal
from ecommerce_cs.main import create_app
from ecommerce_cs.services.chat import sse

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_stream_route_emits_sse_and_releases_slot(db_ready, monkeypatch):
    suffix = f"{uuid.uuid4().int % 10**8:08d}"
    phone = f"199{suffix}"
    async with SessionLocal() as db:
        user = User(
            name="流式测试用户",
            phone_hash=hash_phone(phone),
            phone_masked=mask_phone(phone),
            password_hash="x",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        conversation = Conversation(user_id=user.id, title="流式测试")
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)

    async def fake_stream(user, conversation, message, client_message_id=None):
        yield sse("meta", {"conversation_id": conversation.id, "title": "流式测试"})
        yield sse(
            "done",
            {"message_id": "fake", "content": "好的", "parts": [], "degraded": False},
        )

    monkeypatch.setattr("ecommerce_cs.api.routers.chat.stream_chat", fake_stream)
    ratelimit.reset_state()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/chat/{conversation.id}/stream",
                json={"message": "你好"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.text
        assert "event: meta" in body
        assert "event: done" in body
        assert conversation.id in body

        # 生成结束后槽位必须归还，否则下一次请求会被判定为“上一轮还在生成”
        assert await ratelimit.service.acquire_slot(user.id, 1)
        await ratelimit.release_stream_slot(user.id)
    finally:
        app.dependency_overrides.clear()
        ratelimit.reset_state()
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


async def _seed_user_conversation() -> tuple[User, Conversation]:
    suffix = f"{uuid.uuid4().int % 10**8:08d}"
    phone = f"199{suffix}"
    async with SessionLocal() as db:
        user = User(
            name="流式测试用户",
            phone_hash=hash_phone(phone),
            phone_masked=mask_phone(phone),
            password_hash="x",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        conversation = Conversation(user_id=user.id, title="流式测试")
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
    return user, conversation


async def _cleanup(user: User, conversation: Conversation) -> None:
    async with SessionLocal() as db:
        await db.execute(delete(PendingAction).where(PendingAction.conversation_id == conversation.id))
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
async def test_stream_route_injects_heartbeat(db_ready, monkeypatch):
    user, conversation = await _seed_user_conversation()

    async def slow_stream(user, conversation, message, client_message_id=None):
        await asyncio.sleep(0.12)
        yield sse(
            "done",
            {"message_id": "slow", "content": "完成", "parts": [], "degraded": False},
        )

    monkeypatch.setattr("ecommerce_cs.api.routers.chat.stream_chat", slow_stream)
    monkeypatch.setattr("ecommerce_cs.api.routers.chat.SSE_HEARTBEAT_SECONDS", 0.05)
    ratelimit.reset_state()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/chat/{conversation.id}/stream",
                json={"message": "慢请求"},
            )
        assert response.status_code == 200
        assert ": ping" in response.text
        assert "event: done" in response.text
    finally:
        app.dependency_overrides.clear()
        ratelimit.reset_state()
        await _cleanup(user, conversation)


@pytest.mark.asyncio
async def test_stream_route_blocks_when_pending_action_exists(db_ready):
    user, conversation = await _seed_user_conversation()
    async with SessionLocal() as db:
        await ops_repo.create_pending_action(
            db,
            conversation_id=conversation.id,
            user_id=user.id,
            action_type="cancel_order",
            summary="取消订单",
            payload={"order_no": "IT-DEMO"},
            idempotency_key=f"pending-{uuid.uuid4().hex}",
        )
    ratelimit.reset_state()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/chat/{conversation.id}/stream",
                json={"message": "新消息"},
            )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
    finally:
        app.dependency_overrides.clear()
        ratelimit.reset_state()
        await _cleanup(user, conversation)
