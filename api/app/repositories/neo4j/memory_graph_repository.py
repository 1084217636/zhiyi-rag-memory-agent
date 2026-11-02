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

logger = get_logger(__name__)


def _dt(value: datetime | None) -> str | None:
    """datetime → ISO 字符串（Neo4j 存字符串，避免时区/驱动类型差异）。"""
    return value.isoformat() if isinstance(value, datetime) else value


class MemoryGraphRepository:
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

    async def memory_policy_snapshot(self, user_id: str) -> dict:
        async with self._driver.session() as session:
            revision = await session.run(
                "OPTIONAL MATCH (l:MemoryLedger {user_id:$uid}) RETURN coalesce(l.revision,0) AS revision",
                uid=user_id,
            )
            row = await revision.single()
            result = await session.run(
                """
MATCH (s:Statement {user_id:$uid})
WHERE coalesce(s.status,'active')='active' AND (s.expires_at IS NULL OR s.expires_at > $now)
RETURN s.id AS id,s.statement AS statement,s.embedding AS embedding,
       coalesce(s.memory_kind,s.stmt_type,'FACT') AS memory_kind,
       s.slot_key AS slot_key,s.dialog_at AS dialog_at
ORDER BY s.created_at DESC LIMIT $limit
""",
                uid=user_id,
                now=datetime.now().isoformat(),
                limit=max(1, settings.memory_active_limit),
            )
            return {"revision": int(row["revision"]), "statements": [dict(r) async for r in result]}

    async def memory_policy_status(self, user_id: str) -> dict:
        async with self._driver.session() as session:
            result = await session.run(
                """
MATCH (s:Statement {user_id:$uid})
RETURN coalesce(s.status,'legacy') AS status,count(*) AS count
""",
                uid=user_id,
            )
            counts = {r["status"]: r["count"] async for r in result}
            pending = await session.run(
                """
MATCH (s:Statement {user_id:$uid,status:'pending_confirmation'})
RETURN s.id AS id,s.statement AS statement,s.memory_kind AS kind,
       s.decision_reason AS reason,s.review_targets AS targets
ORDER BY s.created_at DESC LIMIT 50
""",
                uid=user_id,
            )
            return {
                "active_limit": settings.memory_active_limit,
                "graph_node_limit": settings.memory_graph_node_limit,
                "counts": counts,
                "pending": [dict(r) async for r in pending],
            }

    async def memory_operations(self, user_id: str, limit: int = 50) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(
                """
MATCH (o:MemoryOperation {user_id:$uid})
RETURN o.id AS id,o.memory_id AS memory_id,o.action AS action,
       o.target_ids AS target_ids,o.reason AS reason,o.actor_type AS actor_type,
       o.status AS status,o.created_at AS created_at
ORDER BY o.created_at DESC LIMIT $limit
""", uid=user_id, limit=min(max(limit, 1), 100))
            return [dict(row) async for row in result]

    async def purge_memory_source(self, user_id: str, ingestion_id: str, dialogue_id: str | None) -> None:
        """Tombstone and remove source-specific graph facts atomically.

        Shared entities may support other sources and are deliberately retained.
        """
        async def txn(tx):
            now = datetime.now().isoformat()
            await (await tx.run(
                "MERGE (t:MemoryTombstone {id:$ingestion}) "
                "SET t.user_id=$uid,t.deleted_at=$now",
                ingestion=ingestion_id, uid=user_id, now=now,
            )).consume()
            result = await tx.run(
                "MATCH (s:Statement {user_id:$uid,ingestion_id:$ingestion}) "
                "RETURN collect(s.id) AS ids",
                uid=user_id, ingestion=ingestion_id,
            )
            statement_ids = (await result.single())["ids"]
            await (await tx.run(
                """
MATCH (new:Statement {user_id:$uid,ingestion_id:$ingestion})-[:SUPERSEDES]->(old:Statement {user_id:$uid})
WHERE old.status='superseded' AND NOT EXISTS {
  MATCH (other:Statement {user_id:$uid,status:'active'})-[:SUPERSEDES]->(old)
  WHERE other.ingestion_id <> $ingestion
}
SET old.status='active',old.invalid_at=NULL
""",
                uid=user_id, ingestion=ingestion_id,
            )).consume()
            await (await tx.run(
                "MATCH ()-[r:RELATION {user_id:$uid}]->() "
                "WHERE r.statement_id IN $ids DELETE r",
                uid=user_id, ids=statement_ids,
            )).consume()
            await (await tx.run(
                "MATCH (s:Statement {user_id:$uid,ingestion_id:$ingestion}) DETACH DELETE s",
                uid=user_id, ingestion=ingestion_id,
            )).consume()
            await (await tx.run(
                "MATCH (e:Event {user_id:$uid,ingestion_id:$ingestion}) DETACH DELETE e",
                uid=user_id, ingestion=ingestion_id,
            )).consume()
            if dialogue_id:
                await (await tx.run(
                    "MATCH (d:Dialogue {user_id:$uid,id:$dialogue})-[:HAS_CHUNK]->(c:Chunk {user_id:$uid}) "
                    "DETACH DELETE c",
                    uid=user_id, dialogue=dialogue_id,
                )).consume()
                await (await tx.run(
                    "MATCH (d:Dialogue {user_id:$uid,id:$dialogue}) DETACH DELETE d",
                    uid=user_id, dialogue=dialogue_id,
                )).consume()
            await (await tx.run(
                "MATCH (i:MemoryIngestion {id:$ingestion,user_id:$uid}) DETACH DELETE i",
                ingestion=ingestion_id, uid=user_id,
            )).consume()
            await (await tx.run(
                "MATCH (o:MemoryOperation {user_id:$uid,ingestion_id:$ingestion}) "
                "SET o.reason=NULL,o.target_ids=[],o.status='source_deleted'",
                uid=user_id, ingestion=ingestion_id,
            )).consume()
            await (await tx.run(
                "MATCH (e:Entity {user_id:$uid,origin_ingestion:$ingestion}) "
                "WHERE NOT EXISTS { MATCH (:Statement {user_id:$uid})-[:MENTIONS]->(e) } "
                "DETACH DELETE e",
                uid=user_id, ingestion=ingestion_id,
            )).consume()
            await (await tx.run(
                "MERGE (l:MemoryLedger {user_id:$uid}) ON CREATE SET l.revision=0 "
                "SET l.revision=l.revision+1",
                uid=user_id,
            )).consume()
            await (await tx.run(
                "MERGE (o:MemoryOperation {id:$op}) "
                "SET o.user_id=$uid,o.memory_id=$ingestion,o.action='DELETE_SOURCE',"
                "o.target_ids=$ids,o.actor_type='user',o.status='committed',"
                "o.reason='用户删除来源记忆',o.created_at=$now",
                op=f"delete:{ingestion_id}", uid=user_id, ingestion=ingestion_id,
                ids=statement_ids, now=now,
            )).consume()
        async with self._driver.session() as session:
            await session.execute_write(txn)

    async def resolve_memory_statement(self, user_id: str, statement_id: str, action: str) -> bool:
        if action not in {"keep_both", "replace", "reject"}:
            raise ValueError("invalid resolution")

        async def txn(tx):
            result = await tx.run(
                "MATCH (s:Statement {user_id:$uid,id:$id,status:'pending_confirmation'}) RETURN s.memory_kind AS kind,s.review_targets AS targets",
                uid=user_id,
                id=statement_id,
            )
            row = await result.single()
            if not row:
                return False
            await (await tx.run(
                "MERGE (l:MemoryLedger {user_id:$uid}) ON CREATE SET l.revision=0 "
                "SET l.revision=l.revision+1", uid=user_id,
            )).consume()
            if action == "replace":
                # Explicit user confirmation, still scoped to this user's known candidates.
                await tx.run(
                    """
MATCH (old:Statement {user_id:$uid})
WHERE old.id IN $targets AND coalesce(old.memory_kind,old.stmt_type,'FACT')=$kind
MATCH (new:Statement {user_id:$uid,id:$id})
SET old.status='superseded',old.invalid_at=$now
MERGE (new)-[:SUPERSEDES]->(old)
""",
                    uid=user_id,
                    targets=row["targets"] or [],
                    kind=row["kind"],
                    id=statement_id,
                    now=datetime.now().isoformat(),
                )
            await tx.run(
                "MATCH (s:Statement {user_id:$uid,id:$id}) SET s.status=$status,s.reviewed_at=$now",
                uid=user_id,
                id=statement_id,
                status="rejected" if action == "reject" else "active",
                now=datetime.now().isoformat(),
            )
            await (await tx.run(
                """
MERGE (o:MemoryOperation {id:$op})
SET o.user_id=$uid,o.memory_id=$id,o.action=$action,
    o.target_ids=$targets,o.actor_type='user',o.status='committed',
    o.reason='用户确认冲突记忆',o.created_at=$now
""", op=f"resolve:{statement_id}", uid=user_id, id=statement_id,
                action=action, targets=row["targets"] or [], now=datetime.now().isoformat(),
            )).consume()
            await tx.run(
                """
MATCH (s:Statement {user_id:$uid}) WHERE coalesce(s.status,'active')='active'
WITH s ORDER BY coalesce(s.importance,0.5)*coalesce(s.confidence,0.8) DESC,s.created_at DESC,s.id ASC
SKIP $limit SET s.status='archived'
""",
                uid=user_id,
                limit=max(1, settings.memory_active_limit),
            )
            return True

        async with self._driver.session() as session:
            return await session.execute_write(txn)

    async def has_memory_ledger(self, user_id: str) -> bool:
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (l:MemoryLedger {user_id:$uid}) RETURN l.user_id AS id", uid=user_id
            )
            return await result.single() is not None

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
