from __future__ import annotations

from datetime import datetime

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class MemoryGovernanceMixin:
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
