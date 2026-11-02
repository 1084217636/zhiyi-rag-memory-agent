"""记忆检索工具：从记忆图谱召回相关实体与关系。"""
import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.core.agent.tools.base import ToolBuildContext, ToolSpec, register_tool
from app.core.memory.retrieval.searcher import format_memory_context, search_memory

KEY = "memory_search"


class _QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1, max_length=2000, description="检索的问题或关键词")


async def _build(ctx: ToolBuildContext) -> StructuredTool:
    session = ctx.session
    user_id = ctx.user_id
    embed_holder = ctx.embed_holder
    stats_holder = ctx.stats_holder

    async def _run(query: str) -> str:
        embed_client = embed_holder.get("embedding")
        if embed_client is None:
            from app.core.llm.resolver import get_client_for_type

            embed_client = await get_client_for_type(session, user_id, "embedding")
            embed_holder["embedding"] = embed_client
        results = await search_memory(
            embed_client=embed_client, user_id=user_id, query=query, top_k=10
        )
        # 统计：实体数 + 关系数（一跳邻居关系总和）
        relation_count = sum(len(r.get("relations") or []) for r in results)
        stats_holder[KEY] = {
            "entity_count": len(results),
            "relation_count": relation_count,
        }
        if not results:
            return json.dumps({"status": "empty", "evidence": []}, ensure_ascii=False)
        evidence=[]
        for r in results:
            for statement in r.get("statements",[]):
                item={"evidence_id":f"M{len(ctx.citations)+1}","source_id":statement.get("id") or r["id"],
                      "source_type":"memory","doc_name":"用户记忆",
                      "memory_kind":statement.get("memory_kind","FACT"),
                      "content":statement["statement"],"score":r.get("score")}
                ctx.citations.append(item)
                evidence.append(item)
        return json.dumps({"status":"ok","evidence":evidence,
                           "context":format_memory_context(results)},ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=_run,
        name=KEY,
        description="检索关于用户本人的长期记忆（画像、偏好、关系、经历等）。当问题涉及'我'的个人信息时使用。",
        args_schema=_QueryInput,
    )


register_tool(
    ToolSpec(
        key=KEY,
        name="记忆检索",
        description="检索关于你本人的长期记忆（画像、偏好、关系、经历）。",
        icon="🧠",
        builder=_build,
        default_enabled=True,
    )
)
