"""研究成果保存与编辑 API。"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.response import success
from app.db.postgres import get_session
from app.models.user_model import User
from app.schemas.research_schema import ArtifactCreate, ArtifactUpdate
from app.services.research_service import ResearchService

router = APIRouter(prefix="/research/artifacts", tags=["research"])


@router.post("")
async def create_artifact(
    body: ArtifactCreate, user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(await ResearchService(session).create(user.id, body), "研究成果已保存")


@router.get("")
async def list_artifacts(
    kb_id: uuid.UUID | None = Query(default=None), limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
):
    return success(await ResearchService(session).list(user.id, kb_id, limit))


@router.put("/{artifact_id}")
async def update_artifact(
    artifact_id: uuid.UUID, body: ArtifactUpdate,
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
):
    return success(await ResearchService(session).update(user.id, artifact_id, body), "研究成果已更新")
