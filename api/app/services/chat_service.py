"""聚焦 RAG、长期记忆和工具调用的流式问答服务。"""
import json
import uuid
from collections.abc import AsyncGenerator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent.context_hint import current_context_block
from app.core.agent.orchestrator import run_function_calling, run_react
from app.core.agent.tools import build_enabled_tools
from app.core.llm.chat_model import build_default_chat_model, supports_function_call
from app.core.logging import get_logger
from app.models.conversation_model import ROLE_ASSISTANT, ROLE_USER, Conversation, Message
from app.repositories.conversation_repository import ConversationRepository, MessageRepository
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.schemas.chat_schema import ChatStreamRequest

logger = get_logger(__name__)
MAX_HISTORY_TURNS = 20


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ChatService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)

    async def _ensure_conversation(self, user_id: uuid.UUID, body: ChatStreamRequest) -> Conversation:
        if body.conversation_id:
            existing = await self.conversations.get(user_id, body.conversation_id)
            if existing:
                return existing
        title = body.message.strip()[:20] or "新对话"
        return await self.conversations.create(Conversation(user_id=user_id, title=title))

    async def _history(self, conversation_id: uuid.UUID) -> list:
        rows = await self.messages.recent_history(conversation_id, MAX_HISTORY_TURNS)
        result: list = []
        for row in rows:
            if row.role == ROLE_USER:
                result.append(HumanMessage(content=row.content))
            elif row.role == ROLE_ASSISTANT:
                result.append(AIMessage(content=row.content))
        return result

    async def _memory_context(self, user_id: uuid.UUID, query: str) -> str:
        try:
            from app.core.llm.resolver import get_optional_client_for_type
            from app.core.memory.retrieval.active_recall import recall_context

            embed_client = await get_optional_client_for_type(self.session, user_id, "embedding")
            if embed_client is None:
                return ""
            return await recall_context(embed_client=embed_client, user_id=user_id, query=query)
        except Exception as exc:  # 记忆不可用时普通问答仍可继续
            logger.warning("长期记忆召回失败，降级为空: user=%s error=%s", user_id, exc)
            return ""

    async def _dispatch_memory(self, user_id: uuid.UUID, text: str) -> None:
        try:
            from app.models.memory_model import MEMORY_SOURCE_AUTO, Memory
            from app.tasks.memory import extract_memory_task

            memory = Memory(user_id=user_id, raw_text=text, source=MEMORY_SOURCE_AUTO)
            self.session.add(memory)
            await self.session.commit()
            await self.session.refresh(memory)
            extract_memory_task.delay(str(memory.id))
        except Exception as exc:
            logger.warning("记忆抽取任务派发失败，不影响本轮回答: %s", exc)

    async def stream_chat(self, user_id: uuid.UUID, body: ChatStreamRequest) -> AsyncGenerator[str, None]:
        question = body.message.strip()
        conversation = await self._ensure_conversation(user_id, body)
        history = await self._history(conversation.id)
        await self.messages.add(Message(conversation_id=conversation.id, role=ROLE_USER, content=question))
        yield _sse("meta", {"conversation_id": str(conversation.id), "title": conversation.title})

        citations: list[dict] = []
        tool_stats: dict[str, dict] = {}
        overrides = {
            "knowledge_search": body.enable_knowledge,
            "memory_search": body.enable_memory,
            "web_search": body.enable_web_search,
        }
        knowledge_base_ids = await KnowledgeBaseRepository(self.session).list_chat_enabled_ids(user_id)
        tools = await build_enabled_tools(
            self.session,
            user_id,
            citations,
            overrides,
            stats_holder=tool_stats,
            kb_ids=knowledge_base_ids,
        )

        prompt_parts = [
            "你是知忆 AI。优先依据工具返回的证据回答；证据不足时明确说明，不要编造来源。",
            current_context_block(with_tool_hint=body.enable_web_search),
        ]
        if body.enable_memory:
            memory_context = await self._memory_context(user_id, question)
            if memory_context:
                prompt_parts.append(memory_context)
        system_prompt = "\n\n".join(prompt_parts)
        model, config = await build_default_chat_model(self.session, user_id, temperature=0.3, streaming=True)

        full_text = ""
        tool_calls: list[dict] = []
        try:
            if tools and supports_function_call(config):
                messages = [SystemMessage(content=system_prompt), *history, HumanMessage(content=question)]
                events = run_function_calling(model, tools, messages, stats_holder=tool_stats)
            elif tools:
                events = run_react(model, tools, question, history, system_prompt, stats_holder=tool_stats)
            else:
                async def direct_events():
                    messages = [SystemMessage(content=system_prompt), *history, HumanMessage(content=question)]
                    async for chunk in model.astream(messages):
                        if chunk.content:
                            yield {"type": "token", "text": str(chunk.content)}
                events = direct_events()

            async for event in events:
                event_type = event.get("type")
                if event_type == "token":
                    text = event.get("text", "")
                    full_text += text
                    yield _sse("token", {"text": text})
                elif event_type == "tool_start":
                    call = {"tool": event.get("tool", ""), "query": event.get("query", "")}
                    tool_calls.append(call)
                    yield _sse("tool_start", call)
                elif event_type == "tool_result":
                    yield _sse("tool_result", event)

            full_text = full_text.strip() or "本轮没有生成有效回答。"
            await self.messages.add(
                Message(
                    conversation_id=conversation.id,
                    role=ROLE_ASSISTANT,
                    content=full_text,
                    meta_data={"citations": citations, "tool_calls": tool_calls},
                )
            )
            await self.conversations.touch(conversation.id)
            await self._dispatch_memory(user_id, question)
            if citations:
                yield _sse("citation", {"citations": citations})
            yield _sse("done", {"conversation_id": str(conversation.id)})
        except Exception as exc:
            logger.exception("Agent 问答失败: conversation=%s", conversation.id)
            yield _sse("error", {"message": f"生成失败：{exc}"})
