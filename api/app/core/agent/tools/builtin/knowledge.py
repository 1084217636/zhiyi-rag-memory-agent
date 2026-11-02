"""知识库检索工具：检索文档/图片片段并收集引用。"""
import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.core.agent.tools.base import ToolBuildContext, ToolSpec, register_tool

KEY = "knowledge_search"


class _QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1, max_length=2000, description="检索的问题或关键词")


async def _build(ctx: ToolBuildContext) -> StructuredTool:
    session = ctx.session
    user_id = ctx.user_id
    citations = ctx.citations
    stats_holder = ctx.stats_holder
    kb_ids = ctx.kb_ids

    async def _run(query: str) -> str:
        from app.core.rag.search import hybrid_search

        hits = await hybrid_search(session, user_id, query, top_k=5, kb_ids=kb_ids)
        hits = [h for h in hits if "retrieval_signals" not in h or
                h["retrieval_signals"].get("bm25",0)>0 or
                (h["retrieval_signals"].get("cosine") or -1)>=0.45]
        # 统计：命中条数 + 涉及文档数（按 doc_name 去重；无名时按 source_id）
        doc_keys = {(h.get("doc_name") or h.get("source_id")) for h in hits if h}
        stats_holder[KEY] = {
            "hit_count": len(hits),
            "doc_count": len([k for k in doc_keys if k]),
        }
        if not hits:
            return json.dumps({"status":"empty","evidence":[]},ensure_ascii=False)
        evidence=[]
        for h in hits:
            existing=next((c for c in citations if c.get("chunk_id")==h.get("chunk_id") and c.get("source_id")==h.get("source_id")),None)
            item=existing or {
                "evidence_id":f"K{len(citations)+1}","source_id":h.get("source_id"),
                "source_type":h.get("source_type"),"doc_name":h.get("doc_name"),
                "chunk_id":h.get("chunk_id"),"score":h.get("score"),
                "content":h["content"],
            }
            if not existing:
                citations.append(item)
            evidence.append(item)
        return json.dumps({"status":"ok","evidence":evidence},ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=_run,
        name=KEY,
        description="从用户的个人知识库（文档、图片）中检索相关内容。当问题涉及用户上传的资料、文档、笔记时使用。",
        args_schema=_QueryInput,
    )


register_tool(
    ToolSpec(
        key=KEY,
        name="知识库检索",
        description="从你的文档、图片知识库中检索相关内容并带引用来源。",
        icon="🔍",
        builder=_build,
        default_enabled=True,
    )
)
