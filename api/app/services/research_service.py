"""保存与版本化用户确认的研究成果。"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.conversation_model import Conversation
from app.models.document_model import Document
from app.models.knowledge_base_model import KnowledgeBase
from app.models.research_artifact_model import ResearchArtifact
from app.schemas.research_schema import ArtifactCreate, ArtifactUpdate


class ResearchService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _validate_scope(self, user_id: uuid.UUID, kb_id, conversation_id) -> None:
        if kb_id is not None:
            kb = await self.session.scalar(select(KnowledgeBase.id).where(
                KnowledgeBase.id == kb_id, KnowledgeBase.user_id == user_id
            ))
            if kb is None:
                raise BizError("研究空间不存在", code=4041, status_code=404)
        if conversation_id is not None:
            conv = await self.session.scalar(select(Conversation.id).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id
            ))
            if conv is None:
                raise BizError("会话不存在", code=4042, status_code=404)

    async def _validate_citations(self, user_id: uuid.UUID, citations: list[dict]) -> list[dict]:
        normalized = []
        for item in citations:
            if not isinstance(item, dict):
                raise BizError("引用格式错误", code=4001)
            evidence_id = str(item.get("evidence_id") or "").strip()
            source_id = str(item.get("source_id") or "").strip()
            source_type = str(item.get("source_type") or "").strip()
            content = str(item.get("content") or "")
            if not evidence_id or len(evidence_id) > 40 or len(content) > 20_000:
                raise BizError("引用缺少编号或内容过长", code=4001)
            if source_type == "document":
                try:
                    doc_id = uuid.UUID(source_id)
                except ValueError as exc:
                    raise BizError("引用文档不存在", code=4043, status_code=404) from exc
                owner = await self.session.scalar(select(Document.id).where(
                    Document.id == doc_id, Document.user_id == user_id
                ))
                if owner is None:
                    raise BizError("引用文档不存在", code=4043, status_code=404)
            elif source_type not in {"memory", "web"}:
                raise BizError("引用来源类型不支持", code=4001)
            normalized.append({
                "evidence_id": evidence_id,
                "source_id": source_id,
                "source_type": source_type,
                "doc_name": str(item.get("doc_name") or "")[:512],
                "chunk_id": str(item.get("chunk_id") or "")[:128],
                "content": content,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            })
        return normalized

    @staticmethod
    def output(item: ResearchArtifact) -> dict:
        return {
            "id": str(item.id), "title": item.title, "content": item.content,
            "kb_id": str(item.kb_id) if item.kb_id else None,
            "conversation_id": str(item.conversation_id) if item.conversation_id else None,
            "citations": item.citations, "source_snapshot": item.source_snapshot,
            "version": item.version,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }

    async def create(self, user_id: uuid.UUID, body: ArtifactCreate) -> dict:
        await self._validate_scope(user_id, body.kb_id, body.conversation_id)
        citations = await self._validate_citations(user_id, body.citations)
        item = ResearchArtifact(
            user_id=user_id, kb_id=body.kb_id, conversation_id=body.conversation_id,
            title=body.title, content=body.content, citations=citations,
            source_snapshot=[{
                "evidence_id": c["evidence_id"], "source_id": c["source_id"],
                "source_type": c["source_type"], "doc_name": c["doc_name"],
                "chunk_id": c["chunk_id"], "captured_at": c["captured_at"],
            } for c in citations], version=1,
        )
        self.session.add(item)
        await self.session.commit()
        await self.session.refresh(item)
        return self.output(item)

    async def list(self, user_id: uuid.UUID, kb_id: uuid.UUID | None, limit: int = 30) -> list[dict]:
        stmt = select(ResearchArtifact).where(ResearchArtifact.user_id == user_id)
        if kb_id is not None:
            stmt = stmt.where(ResearchArtifact.kb_id == kb_id)
        stmt = stmt.order_by(ResearchArtifact.updated_at.desc()).limit(min(max(limit, 1), 100))
        items = (await self.session.execute(stmt)).scalars().all()
        return [self.output(item) for item in items]

    async def update(self, user_id: uuid.UUID, artifact_id: uuid.UUID, body: ArtifactUpdate) -> dict:
        item = await self.session.scalar(select(ResearchArtifact).where(
            ResearchArtifact.id == artifact_id, ResearchArtifact.user_id == user_id
        ).with_for_update())
        if item is None:
            raise BizError("研究成果不存在", code=4044, status_code=404)
        if item.version != body.expected_version:
            raise BizError("研究成果已被其他请求修改，请刷新后重试", code=4091, status_code=409)
        item.title = body.title
        item.content = body.content
        item.version += 1
        await self.session.commit()
        await self.session.refresh(item)
        return self.output(item)
