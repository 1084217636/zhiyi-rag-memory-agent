"""长期记忆写入、召回、图谱和原始任务管理。"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.response import success
from app.db.postgres import get_session
from app.models.user_model import User
from app.schemas.memory_schema import MemorySearchRequest, RememberRequest, MemoryResolutionRequest
from app.services.memory_service import MemoryService

router = APIRouter(prefix="/memories", tags=["memory"])


@router.post("/remember")
async def remember(body: RememberRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    service = MemoryService(session)
    memory = await service.remember(user.id, body.text)
    return success(service.to_out_dict(memory), "已提交，正在抽取记忆")


@router.post("/search")
async def search_memory(body: MemorySearchRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return success(await MemoryService(session).search(user.id, body.query, body.top_k))


@router.get("/profile")
async def get_profile(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return success(await MemoryService(session).get_profile(user.id))


@router.get("/graph")
async def get_graph(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return success(await MemoryService(session).get_graph(user.id))


@router.get("/graph/entity/{entity_id}")
async def get_entity_subgraph(entity_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return success(await MemoryService(session).get_entity_subgraph(user.id, entity_id))


@router.get("")
async def list_memories(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    service = MemoryService(session)
    items, total = await service.list_memories(user.id, page, page_size)
    return success({"total": total, "page": page, "page_size": page_size, "items": [service.to_out_dict(item) for item in items]})


@router.get("/policy/status")
async def memory_policy_status(user: User = Depends(get_current_user)):
    from app.repositories.neo4j.memory_graph_repository import MemoryGraphRepository
    return success(await MemoryGraphRepository().memory_policy_status(str(user.id)))


@router.get("/policy/operations")
async def memory_policy_operations(user: User = Depends(get_current_user)):
    from app.repositories.neo4j.memory_graph_repository import MemoryGraphRepository
    return success(await MemoryGraphRepository().memory_operations(str(user.id)))


@router.post("/statements/{statement_id}/resolve")
async def resolve_statement(statement_id: str,body: MemoryResolutionRequest,user: User = Depends(get_current_user)):
    from app.repositories.neo4j.memory_graph_repository import MemoryGraphRepository
    from app.core.exceptions import BizError
    changed=await MemoryGraphRepository().resolve_memory_statement(str(user.id),statement_id,body.action)
    if not changed:
        raise BizError("待确认记忆不存在或不属于当前用户",code=3002,status_code=404)
    return success(message="记忆确认已处理")


@router.get("/{memory_id}")
async def get_memory(memory_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    service = MemoryService(session)
    return success(service.to_out_dict(await service.get_detail(user.id, memory_id)))


@router.delete("/{memory_id}")
async def delete_memory(memory_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await MemoryService(session).delete(user.id, memory_id)
    return success(message="删除成功")
