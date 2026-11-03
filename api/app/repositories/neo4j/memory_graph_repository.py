"""记忆图谱仓储：封装 Neo4j 的节点/边写入与检索。

写入用单写事务批量 MERGE，保证一次萃取的图数据原子落库、幂等可重试。
只做数据存取，业务编排在 service / 萃取流水线里完成。
"""

from datetime import datetime
from typing import Any

from app.core.memory.graph_models import (
    ChunkNode,
    DialogueNode,
    EntityNode,
    EventNode,
    InvolvesEdge,
    MentionEdge,
    RelationEdge,
    StatementNode,
)
from app.config import settings
from app.core.logging import get_logger
from app.db.neo4j import get_driver
from app.repositories.neo4j import cypher_queries as cq
from app.repositories.neo4j.memory_governance_repository import MemoryGovernanceMixin
from app.repositories.neo4j.memory_entity_repository import MemoryEntityMixin
from app.repositories.neo4j.memory_insight_repository import MemoryInsightMixin

logger = get_logger(__name__)


def _dt(value: datetime | None) -> str | None:
    """datetime → ISO 字符串（Neo4j 存字符串，避免时区/驱动类型差异）。"""
    return value.isoformat() if isinstance(value, datetime) else value


class MemoryGraphRepository(MemoryGovernanceMixin, MemoryEntityMixin, MemoryInsightMixin):
    """记忆图谱数据访问层。"""

    def __init__(self):
        self._driver = get_driver()

    # ── 序列化：节点/边 → Cypher 参数行 ──

    @staticmethod
    def _dialogue_row(n: DialogueNode) -> dict[str, Any]:
        return {
            "id": n.id,
            "user_id": n.user_id,
            "content": n.content,
            "source": n.source,
            "source_message_id": n.source_message_id,
            "dialog_at": _dt(n.dialog_at),
            "created_at": _dt(n.created_at),
        }

    @staticmethod
    def _chunk_row(n: ChunkNode) -> dict[str, Any]:
        return {
            "id": n.id,
            "user_id": n.user_id,
            "dialog_id": n.dialog_id,
            "content": n.content,
            "speaker": n.speaker,
            "sequence": n.sequence,
            "created_at": _dt(n.created_at),
        }

    @staticmethod
    def _statement_row(n: StatementNode) -> dict[str, Any]:
        return {
            "id": n.id,
            "user_id": n.user_id,
            "chunk_id": n.chunk_id,
            "statement": n.statement,
            "stmt_type": n.stmt_type,
            "temporal_type": n.temporal_type,
            "speaker": n.speaker,
            "valid_at": _dt(n.valid_at),
            "invalid_at": _dt(n.invalid_at),
            "dialog_at": _dt(n.dialog_at),
            "embedding": n.embedding,
            "importance": n.importance,
            "confidence": n.confidence,
            "memory_layer": n.memory_layer,
            "access_count": n.access_count,
            "has_emotional_state": n.has_emotional_state,
            "emotion_type": n.emotion_type,
            "emotion_intensity": n.emotion_intensity,
            "emotion_keywords": n.emotion_keywords,
            "created_at": _dt(n.created_at),
        }

    @staticmethod
    def _entity_row(n: EntityNode) -> dict[str, Any]:
        return {
            "id": n.id,
            "user_id": n.user_id,
            "name": n.name,
            "type": n.type,
            "description": n.description,
            "aliases": n.aliases,
            "name_embedding": n.name_embedding,
            "community_id": n.community_id,
            "importance": n.importance,
            "confidence": n.confidence,
            "memory_layer": n.memory_layer,
            "access_count": n.access_count,
            "mention_count": n.mention_count,
            "connect_strength": n.connect_strength,
            "core_facts": n.core_facts,
            "traits": n.traits,
            "created_at": _dt(n.created_at),
        }

    @staticmethod
    def _event_row(n: EventNode) -> dict[str, Any]:
        return {
            "id": n.id,
            "user_id": n.user_id,
            "title": n.title,
            "description": n.description,
            "event_time": _dt(n.event_time),
            "embedding": n.embedding,
            "created_at": _dt(n.created_at),
        }

    @staticmethod
    def _mention_row(e: MentionEdge) -> dict[str, Any]:
        return {
            "user_id": e.user_id,
            "statement_id": e.statement_id,
            "entity_id": e.entity_id,
            "connect_strength": e.connect_strength,
            "created_at": _dt(e.created_at),
        }

    @staticmethod
    def _relation_row(e: RelationEdge) -> dict[str, Any]:
        return {
            "id": e.id,
            "user_id": e.user_id,
            "source_id": e.source_id,
            "target_id": e.target_id,
            "predicate": e.predicate,
            "predicate_surface": e.predicate_surface,
            "source_text": e.source_text,
            "statement_id": e.statement_id,
            "value": e.value,
            "valid_at": _dt(e.valid_at),
            "invalid_at": _dt(e.invalid_at),
            "importance": e.importance,
            "confidence": e.confidence,
            "access_count": e.access_count,
            "created_at": _dt(e.created_at),
        }

    @staticmethod
    def _involves_row(e: InvolvesEdge) -> dict[str, Any]:
        return {
            "user_id": e.user_id,
            "event_id": e.event_id,
            "entity_id": e.entity_id,
            "role": e.role,
            "created_at": _dt(e.created_at),
        }

    # ── 批量写入：一次萃取的全部图数据，单写事务原子落库 ──

    async def save_graph(
        self,
        *,
        dialogues: list[DialogueNode],
        chunks: list[ChunkNode],
        statements: list[StatementNode],
        entities: list[EntityNode],
        events: list[EventNode],
        mentions: list[MentionEdge],
        relations: list[RelationEdge],
        involves: list[InvolvesEdge],
        policy: dict | None = None,
    ) -> None:
        """按依赖顺序在单事务内写入：节点先于其关系。"""

        async def _txn(tx):
            new_entity_ids = []
            if policy:
                # Acquire the user lock before writes, then check successful replay.
                result = await tx.run(
                    "MERGE (l:MemoryLedger {user_id:$uid}) ON CREATE SET l.revision=0 SET l.locked_at=$now RETURN l.revision AS revision",
                    uid=policy["user_id"],
                    now=datetime.now().isoformat(),
                )
                await result.consume()
                done = await tx.run(
                    "MATCH (i:MemoryIngestion {id:$id,user_id:$uid}) RETURN i.id AS id",
                    id=policy["ingestion_id"],
                    uid=policy["user_id"],
                )
                if await done.single():
                    return
                current = await tx.run(
                    "MATCH (n {user_id:$uid}) RETURN count(n) AS count", uid=policy["user_id"]
                )
                count = (await current.single())["count"]
                ids = list(
                    {
                        n.id
                        for group in [dialogues, chunks, statements, entities, events]
                        for n in group
                    }
                )
                existing = await tx.run(
                    "MATCH (n {user_id:$uid}) WHERE n.id IN $ids RETURN collect(n.id) AS ids",
                    uid=policy["user_id"],
                    ids=ids,
                )
                already = set((await existing.single())["ids"])
                new_entity_ids = [e.id for e in entities if e.id not in already]
                # Include the ingestion marker and conservatively reserve one community
                # node per entity for the existing post-write clustering step. Ledger is counted.
                if (
                    count + len(set(ids) - already) + 1 + len(entities)
                    > settings.memory_graph_node_limit
                ):
                    from app.core.memory.governance.service import MemoryCapacityExceeded

                    raise MemoryCapacityExceeded(
                        "用户图存储预算已达上限；原文保留，需确认保留策略后清理历史"
                    )
            if dialogues:
                await tx.run(cq.DIALOGUE_SAVE, rows=[self._dialogue_row(n) for n in dialogues])
            if chunks:
                await tx.run(cq.CHUNK_SAVE, rows=[self._chunk_row(n) for n in chunks])
            if statements:
                await tx.run(cq.STATEMENT_SAVE, rows=[self._statement_row(n) for n in statements])
            if entities:
                await tx.run(cq.ENTITY_SAVE, rows=[self._entity_row(n) for n in entities])
            if events:
                await tx.run(cq.EVENT_SAVE, rows=[self._event_row(n) for n in events])
            if mentions:
                await tx.run(cq.MENTION_SAVE, rows=[self._mention_row(e) for e in mentions])
            if relations:
                await tx.run(cq.RELATION_SAVE, rows=[self._relation_row(e) for e in relations])
            if involves:
                await tx.run(cq.INVOLVES_SAVE, rows=[self._involves_row(e) for e in involves])

            if policy:
                if new_entity_ids:
                    await (await tx.run(
                        "MATCH (e:Entity {user_id:$uid}) WHERE e.id IN $ids "
                        "SET e.origin_ingestion=$ingestion",
                        uid=policy["user_id"], ids=new_entity_ids,
                        ingestion=policy["ingestion_id"],
                    )).consume()
                if events:
                    await (await tx.run(
                        "MATCH (e:Event {user_id:$uid}) WHERE e.id IN $ids "
                        "SET e.ingestion_id=$ingestion",
                        uid=policy["user_id"], ids=[e.id for e in events],
                        ingestion=policy["ingestion_id"],
                    )).consume()
                from app.core.memory.governance.service import apply_policy

                await apply_policy(tx, policy)

        async with self._driver.session() as session:
            await session.execute_write(_txn)
        logger.info(
            "记忆图谱写入: dialogue=%d chunk=%d statement=%d entity=%d event=%d "
            "mention=%d relation=%d involves=%d",
            len(dialogues),
            len(chunks),
            len(statements),
            len(entities),
            len(events),
            len(mentions),
            len(relations),
            len(involves),
        )


    async def search_active_statements(
        self, user_id: str, vector: list[float], limit: int
    ) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(
                """
MATCH (s:Statement {user_id:$uid})
WHERE s.governed=true AND s.status='active' AND s.embedding IS NOT NULL
  AND (s.expires_at IS NULL OR s.expires_at > $now)
WITH s, vector.similarity.cosine(s.embedding,$vector) AS score
WHERE score >= $minimum
RETURN s.id AS id,s.statement AS statement,s.memory_kind AS memory_kind,
       s.chunk_id AS chunk_id,s.confidence AS confidence,score
ORDER BY score DESC LIMIT $limit
""",
                uid=user_id,
                vector=vector,
                now=datetime.now().isoformat(),
                limit=limit,
                minimum=0.35,
            )
            return [dict(r) async for r in result]

    # ── 去重支持：取用户已有同类实体 ──

    async def list_entities_by_type(self, user_id: str, type_: str) -> list[dict[str, Any]]:
        async with self._driver.session() as session:
            result = await session.run(cq.ENTITY_LIST_BY_TYPE, user_id=user_id, type=type_)
            return [dict(record) async for record in result]

    async def get_entity_by_name(self, user_id: str, name: str) -> dict[str, Any] | None:
        async with self._driver.session() as session:
            result = await session.run(cq.ENTITY_GET_BY_NAME, user_id=user_id, name=name)
            record = await result.single()
            return dict(record) if record else None

    # ── 检索：向量 / 全文 / 邻居遍历 ──

    async def search_entities_by_vector(
        self, user_id: str, vector: list[float], top_k: int
    ) -> list[dict[str, Any]]:
        async with self._driver.session() as session:
            result = await session.run(
                cq.ENTITY_VECTOR_SEARCH, user_id=user_id, vector=vector, top_k=top_k
            )
            return [dict(record) async for record in result]

    async def search_entities_by_fulltext(
        self, user_id: str, query: str, top_k: int
    ) -> list[dict[str, Any]]:
        async with self._driver.session() as session:
            # 注意：Cypher 参数名 $query 与 session.run() 首个位置参数同名，
            # 不能用 query=... 关键字传，否则 "got multiple values for argument 'query'"。
            # 改用 parameters 字典传入参数。
            result = await session.run(
                cq.ENTITY_FULLTEXT_SEARCH,
                {"user_id": user_id, "query": query, "top_k": top_k},
            )
            return [dict(record) async for record in result]

    async def get_entity_neighbors(
        self, user_id: str, entity_ids: list[str]
    ) -> list[dict[str, Any]]:
        async with self._driver.session() as session:
            result = await session.run(
                cq.ENTITY_NEIGHBORS,
                user_id=user_id,
                entity_ids=entity_ids,
                now=datetime.now().isoformat(),
            )
            return [dict(record) async for record in result]

    async def get_entity_supporting_statements(
        self, user_id: str, entity_ids: list[str]
    ) -> list[dict[str, Any]]:
        """取命中实体的原子陈述，保留时间范围和事实溯源语义。"""
        if not entity_ids:
            return []
        async with self._driver.session() as session:
            result = await session.run(
                cq.ENTITY_SUPPORTING_STATEMENTS,
                user_id=user_id,
                entity_ids=entity_ids,
                now=datetime.now().isoformat(),
            )
            return [dict(record) async for record in result]

    async def bump_entity_access(self, user_id: str, entity_ids: list[str]) -> None:
        """检索命中回写：实体 access_count +1、更新 last_access_at。失败不影响检索。"""
        if not entity_ids:
            return
        now = datetime.now().isoformat()
        async with self._driver.session() as session:
            await session.run(
                cq.ENTITY_ACCESS_BUMP, user_id=user_id, entity_ids=entity_ids, now=now
            )

    # ── 记忆巩固（短期→长期 + 画像增强）──


    async def graph_nodes(self, user_id: str) -> list[dict[str, Any]]:
        """全量实体节点。"""
        async with self._driver.session() as session:
            result = await session.run(cq.GRAPH_NODES, user_id=user_id)
            return [dict(r) async for r in result]

    async def graph_edges(self, user_id: str) -> list[dict[str, Any]]:
        """全量实体间关系边。"""
        async with self._driver.session() as session:
            result = await session.run(cq.GRAPH_EDGES, user_id=user_id)
            return [dict(r) async for r in result]

    async def graph_full_nodes(self, user_id: str) -> list[dict[str, Any]]:
        """全量节点（含溯源层：对话/片段/陈述/实体/事件）。"""
        async with self._driver.session() as session:
            result = await session.run(cq.GRAPH_FULL_NODES, user_id=user_id)
            return [dict(r) async for r in result]

    async def graph_full_edges(self, user_id: str) -> list[dict[str, Any]]:
        """全量边（溯源 + 语义：HAS_CHUNK/HAS_STATEMENT/MENTIONS/RELATION/INVOLVES）。"""
        async with self._driver.session() as session:
            result = await session.run(cq.GRAPH_FULL_EDGES, user_id=user_id)
            return [dict(r) async for r in result]

    async def entity_subgraph(
        self, user_id: str, entity_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """单实体一跳子图（中心+邻居 与 它们之间的关系）。"""
        async with self._driver.session() as session:
            nres = await session.run(cq.ENTITY_SUBGRAPH_NODES, user_id=user_id, entity_id=entity_id)
            nodes = [dict(r) async for r in nres]
            eres = await session.run(cq.ENTITY_SUBGRAPH_EDGES, user_id=user_id, entity_id=entity_id)
            edges = [dict(r) async for r in eres]
            return nodes, edges

    async def event_timeline(self, user_id: str) -> list[dict[str, Any]]:
        """事件时间线（按 event_time 倒序）。"""
        async with self._driver.session() as session:
            result = await session.run(cq.EVENT_TIMELINE, user_id=user_id)
            return [dict(r) async for r in result]

    # ── 反思引擎：洞察 Insight ──
