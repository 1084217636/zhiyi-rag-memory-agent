"""会话管理与 SSE 流式 Agent 问答。"""

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.response import success
from app.db.postgres import get_session
from app.models.user_model import User
from app.schemas.chat_schema import (
    ChatStreamRequest,
    ConversationCreateRequest,
    ConversationRenameRequest,
)
from app.services.chat_service import ChatService
from app.services.conversation_service import ConversationService

router = APIRouter(tags=["chat"])


@router.get("/conversations")
async def list_conversations(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    service = ConversationService(session)
    items = await service.list_conversations(user.id)
    return success([service.to_out_dict(item) for item in items])


@router.post("/conversations")
async def create_conversation(
    body: ConversationCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = ConversationService(session)
    conversation = await service.create(user.id, body.title)
    return success(service.to_out_dict(conversation), "已创建")


@router.put("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: ConversationRenameRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = ConversationService(session)
    conversation = await service.rename(user.id, conversation_id, body.title)
    return success(service.to_out_dict(conversation))


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await ConversationService(session).delete(user.id, conversation_id)
    return success(message="删除成功")


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = ConversationService(session)
    return success(await service.list_messages(user.id, conversation_id))


@router.post("/chat/stream")
async def chat_stream(
    body: ChatStreamRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return StreamingResponse(
        ChatService(session).stream_chat(user.id, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
