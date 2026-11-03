from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.repositories.neo4j import cypher_queries as cq

logger = get_logger(__name__)


class MemoryEntityMixin:
    async def promote_short_to_long(
        self,
        user_id: str,
        *,
        min_access: int,
        min_importance: float,
        min_mention: int,
        age_before: str,
    ) -> tuple[int, int]:
        """把达标的短期实体/陈述提升为长期。返回 (实体数, 陈述数)。"""
        now = datetime.now().isoformat()
        async with self._driver.session() as session:
            r1 = await session.run(
                cq.CONSOLIDATE_PROMOTE_ENTITIES,
                user_id=user_id,
                min_access=min_access,
                min_importance=min_importance,
                min_mention=min_mention,
                age_before=age_before,
                now=now,
            )
            ent_cnt = (await r1.single())["cnt"]
            r2 = await session.run(
                cq.CONSOLIDATE_PROMOTE_STATEMENTS,
                user_id=user_id,
                min_access=min_access,
                min_importance=min_importance,
            )
            stmt_cnt = (await r2.single())["cnt"]
            return int(ent_cnt or 0), int(stmt_cnt or 0)

    async def top_long_term_entities(self, user_id: str, top_k: int) -> list[dict[str, Any]]:
        """取 top-K 高频长期实体（供画像增强）。"""
        async with self._driver.session() as session:
            result = await session.run(cq.CONSOLIDATE_TOP_ENTITIES, user_id=user_id, top_k=top_k)
            return [dict(r) async for r in result]

    async def entity_statements(self, user_id: str, entity_id: str) -> list[str]:
        """取某实体关联的陈述文本。"""
        async with self._driver.session() as session:
            result = await session.run(cq.ENTITY_STATEMENTS, user_id=user_id, entity_id=entity_id)
            return [r["statement"] async for r in result if r.get("statement")]

    async def write_entity_profile(
        self, user_id: str, entity_id: str, core_facts: list[str], traits: list[str]
    ) -> None:
        """回写实体画像增强（core_facts / traits）。"""
        now = datetime.now().isoformat()
        async with self._driver.session() as session:
            await session.run(
                cq.ENTITY_WRITE_PROFILE,
                user_id=user_id,
                entity_id=entity_id,
                core_facts=core_facts,
                traits=traits,
                now=now,
            )

    async def count_entities(self, user_id: str) -> int:
        async with self._driver.session() as session:
            result = await session.run(cq.ENTITY_COUNT, user_id=user_id)
            record = await result.single()
            return record["cnt"] if record else 0

    async def list_all_entities(self, user_id: str) -> list[dict[str, Any]]:
        """列出用户全部实体（含一跳出边关系），供画像视图。"""
        async with self._driver.session() as session:
            result = await session.run(cq.ENTITY_LIST_ALL, user_id=user_id)
            return [dict(record) async for record in result]

    async def entity_type_counts(self, user_id: str) -> list[dict[str, Any]]:
        """每种实体类型的数量。"""
        async with self._driver.session() as session:
            result = await session.run(cq.ENTITY_TYPE_COUNTS, user_id=user_id)
            return [dict(record) async for record in result]

    async def delete_entity(self, user_id: str, entity_id: str) -> None:
        """删除单个实体（连带其关系）。"""
        async with self._driver.session() as session:
            await session.run(cq.DELETE_ENTITY, user_id=user_id, entity_id=entity_id)

    # ── V0.0.5 ⑤ 人类反馈纠错 ──

    async def entity_snapshot(self, user_id: str, entity_id: str) -> dict[str, Any] | None:
        """单实体当前快照(写进 memory_corrections.before 用)。"""
        async with self._driver.session() as session:
            result = await session.run(cq.ENTITY_SNAPSHOT, user_id=user_id, entity_id=entity_id)
            record = await result.single()
            return dict(record) if record else None

    async def human_verify_entity(self, user_id: str, entity_id: str) -> bool:
        """用户确认实体正确:human_verified=true + confidence=1.0 + 升长期记忆。"""
        async with self._driver.session() as session:
            result = await session.run(cq.HUMAN_VERIFY_ENTITY, user_id=user_id, entity_id=entity_id)
            return (await result.single()) is not None

    async def correct_entity(
        self,
        user_id: str,
        entity_id: str,
        *,
        name: str | None = None,
        type_: str | None = None,
        description: str | None = None,
        aliases: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """修正实体属性(用户视角的「✏️ 修正」)。任一字段 None 表示不改。"""
        async with self._driver.session() as session:
            result = await session.run(
                cq.CORRECT_ENTITY,
                user_id=user_id,
                entity_id=entity_id,
                name=name,
                type=type_,
                description=description,
                aliases=aliases,
            )
            record = await result.single()
            return dict(record) if record else None

    async def delete_user_graph(self, user_id: str) -> None:
        """删除某用户的全部图数据（数据隔离 / 重置用）。"""
        async with self._driver.session() as session:
            await session.run(cq.DELETE_USER_GRAPH, user_id=user_id)

    async def merge_duplicate_entities(self, user_id: str) -> int:
        """合并历史重复实体（同 user_id + 同名(忽略大小写) + 同类型）。

        取最早创建的为保留方，把其余重复节点的 MENTIONS/INVOLVES/RELATION 边
        改接到保留方，合并别名与描述后删除重复节点。返回被删除的重复节点数。
        """
        removed = 0

        async def _txn(tx):
            nonlocal removed
            result = await tx.run(cq.DUPLICATE_ENTITY_GROUPS, user_id=user_id)
            groups = [dict(r) async for r in result]
            for g in groups:
                ids: list[str] = g["ids"]
                keeper_id = ids[0]
                dup_ids = ids[1:]
                if not dup_ids:
                    continue
                # 合并别名：保留方名 + 所有节点名/别名（去掉保留方自身名）
                names: list[str] = g.get("names") or []
                aliases_list: list[list] = g.get("aliases_list") or []
                keeper_name = names[0] if names else ""
                alias_set: set[str] = set()
                for nm in names[1:]:
                    if nm:
                        alias_set.add(nm)
                for al in aliases_list:
                    for a in al or []:
                        if a:
                            alias_set.add(a)
                alias_set.discard(keeper_name)
                # 描述取最长
                descs = [d for d in (g.get("descs") or []) if d]
                description = max(descs, key=len) if descs else ""

                await tx.run(
                    cq.DEDUP_REDIRECT_MENTIONS,
                    user_id=user_id,
                    keeper_id=keeper_id,
                    dup_ids=dup_ids,
                )
                await tx.run(
                    cq.DEDUP_REDIRECT_INVOLVES,
                    user_id=user_id,
                    keeper_id=keeper_id,
                    dup_ids=dup_ids,
                )
                await tx.run(
                    cq.DEDUP_REDIRECT_RELATION_OUT,
                    user_id=user_id,
                    keeper_id=keeper_id,
                    dup_ids=dup_ids,
                )
                await tx.run(
                    cq.DEDUP_REDIRECT_RELATION_IN,
                    user_id=user_id,
                    keeper_id=keeper_id,
                    dup_ids=dup_ids,
                )
                await tx.run(
                    cq.DEDUP_UPDATE_KEEPER,
                    user_id=user_id,
                    keeper_id=keeper_id,
                    aliases=sorted(alias_set),
                    description=description,
                )
                await tx.run(
                    cq.DEDUP_DELETE_DUPS,
                    user_id=user_id,
                    dup_ids=dup_ids,
                )
                removed += len(dup_ids)

        async with self._driver.session() as session:
            await session.execute_write(_txn)
        logger.info("重复实体合并完成: user=%s removed=%d", user_id, removed)
        return removed

    # ── 知识图谱可视化 / 时间线（阶段8）──
