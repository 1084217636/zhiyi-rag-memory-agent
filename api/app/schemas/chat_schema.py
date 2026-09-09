"""知忆 AI 对话请求结构。"""
import uuid

from pydantic import BaseModel, Field


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="新对话", max_length=256)


class ConversationRenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)


class ChatStreamRequest(BaseModel):
    """发送消息；conversation_id 为空时自动创建会话。"""

    conversation_id: uuid.UUID | None = None
    message: str = Field(..., min_length=1, max_length=20_000)
    enable_knowledge: bool = True
    enable_memory: bool = True
    enable_web_search: bool = False
