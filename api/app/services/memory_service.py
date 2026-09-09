"""长期记忆业务：写入、检索、画像与图谱查询。"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.logging import get_logger
from app.models.memory_model import MEMORY_SOURCE_MANUAL, MEMORY_STATUS_PENDING, Memory
from app.repositories.memory_repository import MemoryRepository

logger = get_logger(__name__)


class MemoryService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = MemoryRepository(session)

    async def remember(self, user_id: uuid.UUID, text: str) -> Memory:
        """保存原始记忆，再交给 Celery 异步抽取图谱事实。"""
        text = (text or "").strip()
        if not text:
            raise BizError("记忆内容不能为空", code=3001)
        memory = await self.repo.create(Memory(
            user_id=user_id, raw_text=text,
            source=MEMORY_SOURCE_MANUAL, status=MEMORY_STATUS_PENDING,
        ))
        from app.tasks.memory import extract_memory_task
        extract_memory_task.delay(str(memory.id))
        logger.info("主动记住已提交萃取: memory=%s", memory.id)
        return memory

    async def get_detail(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> Memory:
        memory = await self.repo.get_by_id(memory_id)
        if not memory or memory.user_id != user_id:
            raise BizError("记忆不存在", code=3002, status_code=404)
        return memory

    async def list_memories(self, user_id: uuid.UUID, page: int, page_size: int) -> tuple[list[Memory], int]:
        return await self.repo.list_by_user(user_id, page, page_size)

    async def delete(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> None:
        await self.repo.delete(await self.get_detail(user_id, memory_id))

    async def search(self, user_id: uuid.UUID, query: str, top_k: int = 10) -> list[dict]:
        """使用向量、全文与图邻居进行混合召回。"""
        from app.core.llm.resolver import get_client_for_type
        from app.core.memory.retrieval.searcher import search_memory
        embed_client = await get_client_for_type(self.session, user_id, "embedding")
        return await search_memory(embed_client=embed_client, user_id=user_id, query=query, top_k=top_k)

    async def get_profile(self, user_id: uuid.UUID) -> dict:
        from app.repositories.neo4j.memory_graph_repository import MemoryGraphRepository
        repo = MemoryGraphRepository()
        repo_factory = getattr(self, "_memory_graph_repo_factory", None)
        if repo_factory is not None:
            repo = repo_factory()
        uid = str(user_id)
        entities = await repo.list_all_entities(uid)
        counts = await repo.entity_type_counts(uid)
        groups: dict[str, list[dict]] = {}
        for entity in entities:
            item = {
                "id": entity.get("id"), "name": entity.get("name"),
                "type": entity.get("type"), "description": entity.get("description") or "",
                "aliases": entity.get("aliases") or [], "relations": entity.get("relations") or [],
                "importance": entity.get("importance", 0.5), "confidence": entity.get("confidence", 0.8),
                "memory_layer": entity.get("memory_layer") or "short_term",
                "access_count": entity.get("access_count", 0), "mention_count": entity.get("mention_count", 1),
                "core_facts": entity.get("core_facts") or [], "traits": entity.get("traits") or [],
            }
            groups.setdefault(item["type"], []).append(item)
        return {
            "total": len(entities),
            "type_counts": {row["type"]: row["cnt"] for row in counts},
            "groups": [{"type": name, "entities": items} for name, items in groups.items()],
        }

    async def get_graph(self, user_id: uuid.UUID) -> dict:
        from app.repositories.neo4j.community_repository import CommunityRepository
        from app.repositories.neo4j.memory_graph_repository import MemoryGraphRepository
        uid = str(user_id)
        repo = MemoryGraphRepository()
        raw_nodes = await repo.graph_full_nodes(uid)
        raw_edges = await repo.graph_full_edges(uid)

        def display_name(kind: str, name: str | None) -> str:
            value = (name or "").strip().replace("\n", " ")
            return value if kind in ("Entity", "Event") else value[:24] + ("…" if len(value) > 24 else "")

        return {
            "nodes": [{
                "id": node.get("id"), "kind": node.get("kind") or "Entity",
                "name": display_name(node.get("kind") or "Entity", node.get("name")),
                "type": node.get("type"), "description": node.get("description") or "",
                "community_id": node.get("community_id"), "importance": node.get("importance", 0.5),
                "memory_layer": node.get("memory_layer") or "short_term",
                "access_count": node.get("access_count", 0), "mention_count": node.get("mention_count", 1),
                "aliases": node.get("aliases") or [], "core_facts": node.get("core_facts") or [],
                "traits": node.get("traits") or [],
            } for node in raw_nodes],
            "edges": [{
                "source": edge.get("source"), "target": edge.get("target"),
                "rel": edge.get("rel") or "", "predicate": edge.get("predicate") or "",
                "predicate_surface": edge.get("predicate_surface") or "",
            } for edge in raw_edges if edge.get("source") and edge.get("target")],
            "communities": await CommunityRepository().list_communities(uid),
        }

    async def get_entity_subgraph(self, user_id: uuid.UUID, entity_id: str) -> dict:
        from app.repositories.neo4j.memory_graph_repository import MemoryGraphRepository
        raw_nodes, raw_edges = await MemoryGraphRepository().entity_subgraph(str(user_id), entity_id)
        return {
            "center": entity_id,
            "nodes": [{
                "id": node.get("id"), "name": node.get("name"), "type": node.get("type"),
                "description": node.get("description") or "", "community_id": node.get("community_id"),
            } for node in raw_nodes],
            "edges": [{
                "source": edge.get("source"), "target": edge.get("target"),
                "predicate": edge.get("predicate") or "",
                "predicate_surface": edge.get("predicate_surface") or "",
            } for edge in raw_edges if edge.get("source") and edge.get("target")],
        }

    @staticmethod
    def to_out_dict(memory: Memory) -> dict:
        return {
            "id": str(memory.id), "raw_text": memory.raw_text, "source": memory.source,
            "status": memory.status, "error_msg": memory.error_msg,
            "graph_stats": memory.graph_stats,
            "created_at": memory.created_at.isoformat() if memory.created_at else None,
        }
