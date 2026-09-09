"""知忆 AI 的公开 API：只暴露知识库、记忆、Agent 和运行所需基础能力。"""
from fastapi import APIRouter

from app.controllers import (
    auth_controller,
    chat_controller,
    document_controller,
    file_controller,
    health_controller,
    knowledge_base_controller,
    mcp_controller,
    memory_controller,
    model_config_controller,
    tool_controller,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health_controller.router)
api_router.include_router(auth_controller.router)
api_router.include_router(model_config_controller.router)
api_router.include_router(knowledge_base_controller.router)
api_router.include_router(document_controller.router)
api_router.include_router(file_controller.router)
api_router.include_router(memory_controller.router)
api_router.include_router(chat_controller.router)
api_router.include_router(mcp_controller.router)
api_router.include_router(tool_controller.router)
