"""对话路由：SSE 流式、审批与满意度反馈。"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.api.deps import get_current_user
from ecommerce_cs.core.errors import ConflictError, ForbiddenError, NotFoundError
from ecommerce_cs.core.ratelimit import (
    acquire_stream_slot,
    enforce_chat_rate,
    release_stream_slot,
)
from ecommerce_cs.db.models import User
from ecommerce_cs.db.repositories import chat as chat_repo
from ecommerce_cs.db.repositories import ops as ops_repo
from ecommerce_cs.db.session import get_db
from ecommerce_cs.schemas.chat import (
    ApprovalRequest,
    ChatRequest,
    FeedbackRequest,
    PendingActionOut,
)
from ecommerce_cs.services.chat import (
    conversation_lock,
    resolve_approval,
    stream_chat,
)

router = APIRouter(prefix="/chat", tags=["chat"])
messages_router = APIRouter(prefix="/messages", tags=["messages"])

# 长时间无输出时向 SSE 注入注释行，避免反向代理按空闲超时断流
SSE_HEARTBEAT_SECONDS = 15.0


async def _require_conversation(db: AsyncSession, conversation_id: str, user: User):
    conversation = await chat_repo.get_conversation(db, conversation_id, user.id)
    if not conversation:
        raise NotFoundError("会话不存在")
    return conversation


async def _stream_with_slot(
    user: User,
    conversation,
    message: str,
    client_message_id: str | None,
    lock: asyncio.Lock,
) -> AsyncIterator[str]:
    iterator: AsyncGenerator[str, None] = stream_chat(
        user, conversation, message, client_message_id
    )
    pending: asyncio.Future[str] = asyncio.ensure_future(anext(iterator))
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=SSE_HEARTBEAT_SECONDS)
            if not done:
                yield ": ping\n\n"
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                break
            yield chunk
            pending = asyncio.ensure_future(anext(iterator))
    finally:
        if not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        await iterator.aclose()
        await release_stream_slot(user.id)
        if lock.locked():
            lock.release()


@router.post("/{conversation_id}/stream")
async def chat_stream(
    conversation_id: str,
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    conversation = await _require_conversation(db, conversation_id, user)
    pending_action = await ops_repo.get_latest_pending(db, conversation.id)
    if pending_action:
        raise ConflictError("请先处理当前会话的待确认操作，再发送新消息")

    await enforce_chat_rate(user.id)
    lock = conversation_lock(conversation_id)
    if lock.locked():
        raise ConflictError("该会话正在生成回复，请稍后再试")
    await lock.acquire()
    slot_acquired = False
    try:
        await acquire_stream_slot(user.id)
        slot_acquired = True
        return StreamingResponse(
            _stream_with_slot(
                user,
                conversation,
                payload.message,
                payload.client_message_id,
                lock,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception:
        if slot_acquired:
            await release_stream_slot(user.id)
        if lock.locked():
            lock.release()
        raise


@router.get("/{conversation_id}/pending", response_model=PendingActionOut)
async def pending_action(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PendingActionOut:
    conversation = await _require_conversation(db, conversation_id, user)
    action = await ops_repo.get_latest_pending(db, conversation.id)
    if not action:
        return PendingActionOut(has_pending=False)
    return PendingActionOut(
        has_pending=True,
        action_id=action.id,
        type=action.type,
        summary=action.summary,
        payload=action.payload or {},
    )


@router.post("/{conversation_id}/approval")
async def approval(
    conversation_id: str,
    payload: ApprovalRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    conversation = await _require_conversation(db, conversation_id, user)
    lock = conversation_lock(conversation_id)
    if lock.locked():
        raise ConflictError("该会话正在生成回复，请稍后再确认操作")
    async with lock:
        return await resolve_approval(user, conversation, payload.decision)


@messages_router.post("/{message_id}/feedback")
async def submit_feedback(
    message_id: str,
    payload: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    message = await chat_repo.get_message(db, message_id)
    if not message:
        raise NotFoundError("消息不存在")
    conversation = await chat_repo.get_conversation(db, message.conversation_id, user.id)
    if not conversation:
        raise ForbiddenError("无权操作该消息")

    feedback = await ops_repo.add_feedback(
        db,
        message_id=message.id,
        conversation_id=conversation.id,
        user_id=user.id,
        rating=payload.rating,
        comment=payload.comment,
    )
    return {"message": "感谢您的反馈", "rating": feedback.rating}
