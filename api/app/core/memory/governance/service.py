"""Build reconciliation plans; commit is inside the existing graph transaction."""

import difflib
from datetime import datetime

from app.config import settings
from app.core.memory.governance.policy import classify_kind, decide, expiry, fact_slot, validate_sources


def _similarity(new: dict, old: dict) -> float:
    a, b = new.get("embedding"), old.get("embedding")
    cosine = 0.0
    if a and b and len(a) == len(b):
        denom = (sum(x * x for x in a) * sum(x * x for x in b)) ** 0.5
        cosine = sum(x * y for x, y in zip(a, b)) / denom if denom else 0
    return max(cosine, difflib.SequenceMatcher(None, new["statement"], old["statement"]).ratio())


async def prepare_policy(
    repo, client, embed_client, user_id, statements, ingestion_id, source_text=None
):
    snapshot = await repo.memory_policy_snapshot(user_id)
    existing = list(snapshot["statements"])
    for row in existing:
        row["slot_key"] = row.get("slot_key") or fact_slot(row["statement"], row["memory_kind"])
    vectors = await embed_client.embed([s.statement for s in statements]) if statements else []
    if len(vectors) != len(statements):
        raise ValueError("陈述向量数量不匹配")
    supported = None
    if source_text is not None:
        supported = await validate_sources(
            client,
            source_text,
            [
                {
                    "id": s.id,
                    "statement": s.statement,
                    "memory_kind": classify_kind(s.statement, s.stmt_type, s.temporal_type),
                }
                for s in statements
            ],
        )
    plans = []
    for index, (stmt, vector) in enumerate(zip(statements, vectors)):
        # IDs remain stable across re-delivery of the same PG memory task.
        kind = classify_kind(stmt.statement, stmt.stmt_type, stmt.temporal_type)
        candidate = {
            "id": stmt.id,
            "statement": stmt.statement,
            "memory_kind": kind,
            "embedding": vector,
            "confidence": stmt.confidence,
            "dialog_at": stmt.dialog_at.isoformat() if stmt.dialog_at else None,
            "slot_key": fact_slot(stmt.statement, kind),
            "source_text": source_text or stmt.statement,
        }
        ranked = sorted(existing, key=lambda old: (
            bool(candidate["slot_key"] and old.get("slot_key") == candidate["slot_key"]),
            _similarity(candidate, old)), reverse=True)
        candidates = [r for r in ranked if (
            candidate["slot_key"] and r.get("slot_key") == candidate["slot_key"]
        ) or _similarity(candidate, r) >= 0.25][
            : settings.memory_candidate_limit
        ]
        if supported is not None and stmt.id not in supported:
            from app.core.memory.governance.policy import MemoryDecision

            decision = MemoryDecision(
                action="CONFLICT", confidence=0, reason="原文支持检查未通过，等待确认"
            )
        else:
            decision = await decide(client, candidate, candidates)
        status = {
            "ADD": "active",
            "UPDATE": "active",
            "NOOP": "duplicate",
            "CONFLICT": "pending_confirmation",
            "SKIP": "rejected",
        }[decision.action]
        stmt.embedding = vector
        plans.append(
            {
                "id": stmt.id,
                "action": decision.action,
                "targets": decision.target_ids,
                "status": status,
                "memory_kind": kind,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "expires_at": expiry(kind, datetime.now(), stmt.statement),
                "slot_key": candidate["slot_key"],
                "ingestion_id": ingestion_id,
            }
        )
        if status == "active":
            retired = set(decision.target_ids) if decision.action == "UPDATE" else set()
            existing = [r for r in existing if r["id"] not in retired] + [candidate]
    return {
        "user_id": user_id,
        "revision": snapshot["revision"],
        "plans": plans,
        "active_limit": max(1, settings.memory_active_limit),
        "ingestion_id": ingestion_id,
    }


class MemoryCapacityExceeded(RuntimeError):
    """Source stays in PostgreSQL; no graph write when storage budget is exceeded."""


class MemoryRevisionConflict(RuntimeError):
    """The snapshot changed; retry ingestion from a fresh snapshot."""


class MemorySourceDeleted(RuntimeError):
    """A deleted source cannot be recreated by a delayed Celery task."""


async def apply_policy(tx, policy):
    """Called in the same transaction as graph writes, with a per-user write lock."""
    uid = policy["user_id"]
    now = datetime.now().isoformat()
    tombstone = await tx.run(
        "MATCH (t:MemoryTombstone {id:$id,user_id:$uid}) RETURN t.id AS id",
        id=policy["ingestion_id"], uid=uid,
    )
    if await tombstone.single():
        raise MemorySourceDeleted("记忆来源已删除，不允许异步任务重新写入")
    rows = await tx.run(
        """
MERGE (l:MemoryLedger {user_id:$uid})
ON CREATE SET l.revision=0
SET l.locked_at=$now
RETURN l.revision AS revision
""",
        uid=uid,
        now=now,
    )
    row = await rows.single()
    # Re-read after acquiring the write lock. Existing ingestion marker makes
    # successful redeliveries a no-op (caller checks it before any graph writes).
    if int(row["revision"]) != policy["revision"]:
        raise MemoryRevisionConflict("记忆已被其他任务更新，需要重新对账")
    for plan in policy["plans"]:
        await tx.run(
            """
MATCH (s:Statement {user_id:$uid,id:$id})
SET s.memory_kind=$kind, s.status=$status, s.expires_at=$expires,
    s.slot_key=$slot, s.asserted_at=$now,
    s.decision_reason=$reason, s.decision_confidence=$confidence,
    s.ingestion_id=$ingestion, s.governed=true, s.review_targets=$targets
""",
            uid=uid,
            id=plan["id"],
            kind=plan["memory_kind"],
            status=plan["status"],
            expires=plan["expires_at"],
            reason=plan["reason"],
            confidence=plan["confidence"],
            slot=plan["slot_key"],
            now=now,
            ingestion=policy["ingestion_id"],
            targets=plan["targets"],
        )
        await tx.run(
            """
MERGE (o:MemoryOperation {id:$op_id})
SET o.user_id=$uid,o.memory_id=$id,o.action=$action,o.target_ids=$targets,
    o.reason=$reason,o.actor_type='agent',o.status='committed',
    o.ingestion_id=$ingestion,o.created_at=$now
""",
            op_id=f"{policy['ingestion_id']}:{plan['id']}", uid=uid, id=plan["id"],
            action=plan["action"], targets=plan["targets"],
            reason=plan["reason"], ingestion=policy["ingestion_id"], now=now,
        )
        if plan["action"] == "UPDATE":
            await tx.run(
                """
MATCH (old:Statement {user_id:$uid}) WHERE old.id IN $ids
MATCH (new:Statement {user_id:$uid,id:$new_id})
SET old.status='superseded',old.invalid_at=$now
MERGE (new)-[:SUPERSEDES]->(old)
""",
                uid=uid,
                ids=plan["targets"],
                new_id=plan["id"],
                now=now,
            )
        if plan["action"] == "NOOP":
            await tx.run(
                """
MATCH (old:Statement {user_id:$uid}) WHERE old.id IN $ids
MATCH (new:Statement {user_id:$uid,id:$new_id})
SET old.mention_count=coalesce(old.mention_count,1)+1
MERGE (new)-[:SUPPORTS]->(old)
""",
                uid=uid,
                ids=plan["targets"],
                new_id=plan["id"],
            )
    await tx.run(
        """
MATCH (s:Statement {user_id:$uid})
WHERE coalesce(s.status,'active')='active' AND s.expires_at IS NOT NULL AND s.expires_at <= $now
SET s.status='expired'
""",
        uid=uid,
        now=now,
    )
    # Archive instead of deleting original statements, relationships or sources.
    await tx.run(
        """
MATCH (s:Statement {user_id:$uid}) WHERE coalesce(s.status,'active')='active'
WITH s ORDER BY coalesce(s.importance,0.5)*coalesce(s.confidence,0.8) DESC,
               coalesce(s.access_count,0) DESC,s.created_at DESC,s.id ASC
SKIP $limit SET s.status='archived', s.archived_at=$now
""",
        uid=uid,
        limit=policy["active_limit"],
        now=now,
    )
    await tx.run(
        """
MATCH (l:MemoryLedger {user_id:$uid}) SET l.revision=l.revision+1
MERGE (i:MemoryIngestion {id:$ingestion}) SET i.user_id=$uid,i.completed_at=$now
""",
        uid=uid,
        ingestion=policy["ingestion_id"],
        now=now,
    )
