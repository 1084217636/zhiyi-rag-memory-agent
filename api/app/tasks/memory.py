"""记忆萃取 Celery 任务：取 memories 原文 → 萃取流水线 → 写 Neo4j → 回写状态。

与文档解析任务一致：任务为同步入口，内部用 asyncio.run 跑异步；
每个任务用任务级 DB 引擎（NullPool）避免事件循环绑定问题。
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.models  # noqa: F401  确保所有 ORM 模型注册到 metadata
from app.celery_app import celery_app
from app.core.llm.resolver import get_client_for_type
from app.core.logging import get_logger
from app.core.memory.extraction.orchestrator import run_extraction
from app.db import neo4j, redis
from app.core.llm.client import close_llm_client
from app.core.memory.governance.service import MemoryRevisionConflict
from app.db.postgres import create_task_engine
from app.models.memory_model import (
    MEMORY_STATUS_DONE,
    MEMORY_STATUS_EXTRACTING,
    MEMORY_STATUS_FAILED,
    MEMORY_STATUS_PENDING,
    Memory,
)
from app.repositories.memory_repository import MemoryRepository

logger = get_logger(__name__)


async def _run(memory_id: str, allow_conflict_retry: bool = True) -> None:
    mem_uuid = uuid.UUID(memory_id)
    engine = create_task_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_maker() as session:
            await _extract(session, mem_uuid, allow_conflict_retry=allow_conflict_retry)
    finally:
        await engine.dispose()
        # 关闭本任务事件循环内创建的 Neo4j 驱动
        await neo4j.close()
        await redis.close()
        await close_llm_client()


async def _extract(session: AsyncSession, mem_uuid: uuid.UUID, *, allow_conflict_retry: bool = True) -> None:
    # Duplicate broker deliveries are expected when pending jobs are replayed.
    # Only one worker may move this source record into extraction.
    claimed = await session.execute(
        update(Memory)
        .where(Memory.id == mem_uuid, Memory.status == MEMORY_STATUS_PENDING)
        .values(status=MEMORY_STATUS_EXTRACTING)
    )
    await session.commit()
    if claimed.rowcount != 1:
        return
    repo = MemoryRepository(session)
    memory = await repo.get_by_id(mem_uuid)
    if not memory:
        logger.warning("萃取任务：记忆不存在 %s", mem_uuid)
        return
    if memory.status == MEMORY_STATUS_DONE:
        return
    try:
        chat_client = await get_client_for_type(session, memory.user_id, "chat")
        embed_client = await get_client_for_type(session, memory.user_id, "embedding")

        stats = await run_extraction(
            chat_client=chat_client,
            embed_client=embed_client,
            user_id=str(memory.user_id),
            text=memory.raw_text,
            source=memory.source,
            ingestion_id=str(memory.id),
            source_message_id=(
                str(memory.source_message_id) if memory.source_message_id else None
            ),
        )

        updated = await session.execute(
            update(Memory).where(Memory.id == mem_uuid, Memory.status == MEMORY_STATUS_EXTRACTING)
            .values(status=MEMORY_STATUS_DONE, graph_dialogue_id=stats.dialogue_id,
                    graph_stats=stats.to_dict(), error_msg=None)
        )
        if updated.rowcount == 1:
            memory.status = MEMORY_STATUS_DONE
        await session.commit()
        logger.info("记忆萃取完成: %s %s", mem_uuid, stats.to_dict())
    except Exception as e:
        logger.error("记忆萃取失败: %s: %s", mem_uuid, e, exc_info=True)
        next_status = (
            MEMORY_STATUS_PENDING
            if isinstance(e, MemoryRevisionConflict) and allow_conflict_retry
            else MEMORY_STATUS_FAILED
        )
        updated = await session.execute(
            update(Memory).where(Memory.id == mem_uuid, Memory.status == MEMORY_STATUS_EXTRACTING)
            .values(status=next_status, error_msg=str(e)[:500])
        )
        if updated.rowcount == 1:
            memory.status = next_status
        await session.commit()
        if isinstance(e, MemoryRevisionConflict) and allow_conflict_retry:
            raise


@celery_app.task(name="app.tasks.memory.extract_memory", bind=True, autoretry_for=(MemoryRevisionConflict,), retry_backoff=True, max_retries=2)
def extract_memory_task(self, memory_id: str) -> str:
    """记忆萃取的 Celery 任务入口。"""
    asyncio.run(_run(memory_id, allow_conflict_retry=self.request.retries < self.max_retries))
    return memory_id


async def _requeue_pending_memories() -> int:
    """Recover committed memory sources whose initial broker publish was lost."""
    engine = create_task_engine()
    try:
        session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with session_maker() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
            result = await session.execute(
                select(Memory.id)
                .where(Memory.status == MEMORY_STATUS_PENDING, Memory.created_at <= cutoff)
                .order_by(Memory.created_at)
                .limit(100)
            )
            ids = list(result.scalars())
        for memory_id in ids:
            extract_memory_task.apply_async(args=(str(memory_id),), queue="memory")
        return len(ids)
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.memory.requeue_pending")
def requeue_pending_memories_task() -> int:
    return asyncio.run(_requeue_pending_memories())
