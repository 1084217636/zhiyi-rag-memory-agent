from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.repositories.neo4j import cypher_queries as cq

logger = get_logger(__name__)


class MemoryInsightMixin:
    async def reflection_top_entities(self, user_id: str, top_k: int) -> list[dict[str, Any]]:
        """取反思输入：top-N 高重要度/高频实体。"""
        async with self._driver.session() as session:
            result = await session.run(cq.REFLECTION_TOP_ENTITIES, user_id=user_id, top_k=top_k)
            return [dict(r) async for r in result]

    async def reflection_entity_statements(
        self, user_id: str, entity_id: str, limit: int = 5
    ) -> list[str]:
        """取某实体的代表性陈述（按重要度倒序，少量）。"""
        async with self._driver.session() as session:
            result = await session.run(
                cq.REFLECTION_ENTITY_STATEMENTS,
                user_id=user_id,
                entity_id=entity_id,
                limit=limit,
            )
            return [r["statement"] async for r in result if r.get("statement")]

    async def upsert_insight(
        self,
        *,
        user_id: str,
        theme: str,
        content: str,
        embedding: list[float] | None,
        importance: float,
        confidence: float,
        source_count: int,
        entity_ids: list[str],
    ) -> str:
        """按 theme upsert 洞察（同主题更新而非新建）+ 重建 DERIVED_FROM 边。返回 insight id。"""
        import uuid as _uuid

        now = datetime.now().isoformat()
        async with self._driver.session() as session:
            # 查同主题已有洞察
            r = await session.run(cq.INSIGHT_GET_BY_THEME, user_id=user_id, theme=theme)
            rec = await r.single()
            insight_id = rec["id"] if rec else _uuid.uuid4().hex
            # upsert
            await session.run(
                cq.INSIGHT_UPSERT,
                id=insight_id,
                user_id=user_id,
                theme=theme,
                content=content,
                embedding=embedding,
                importance=importance,
                confidence=confidence,
                source_count=source_count,
                now=now,
            )
            # 重建来源边（先清后建，保持最新）
            await session.run(cq.INSIGHT_CLEAR_DERIVED, user_id=user_id, insight_id=insight_id)
            if entity_ids:
                await session.run(
                    cq.INSIGHT_LINK_ENTITIES,
                    user_id=user_id,
                    insight_id=insight_id,
                    entity_ids=entity_ids,
                    now=now,
                )
            return insight_id

    async def list_insights(self, user_id: str) -> list[dict[str, Any]]:
        """列出用户全部洞察。"""
        async with self._driver.session() as session:
            result = await session.run(cq.INSIGHT_LIST, user_id=user_id)
            return [dict(r) async for r in result]

    async def count_insights(self, user_id: str) -> int:
        async with self._driver.session() as session:
            result = await session.run(cq.INSIGHT_COUNT, user_id=user_id)
            record = await result.single()
            return record["cnt"] if record else 0

    async def delete_insight(self, user_id: str, insight_id: str) -> None:
        async with self._driver.session() as session:
            await session.run(cq.INSIGHT_DELETE, user_id=user_id, insight_id=insight_id)

    async def search_insights_by_vector(
        self, user_id: str, vector: list[float], top_k: int
    ) -> list[dict[str, Any]]:
        """洞察向量召回（供 ③ 主动召回）。"""
        async with self._driver.session() as session:
            result = await session.run(
                cq.INSIGHT_VECTOR_SEARCH,
                user_id=user_id,
                vector=vector,
                top_k=top_k,
            )
            return [dict(r) async for r in result]
