"""Agent 编排：方案B 双路径工具循环，产出统一事件流。

- 强模型（支持 function calling）：bind_tools + 流式工具循环，原生决定调用哪个工具。
- 弱模型：ToolOrchestrator（prompt 模拟 ReAct），解析 Action/Action Input 手动调工具。

两条路径都产出统一事件 dict：
  {"type": "tool_start", "tool", "query"} /
  {"type": "tool_result", "tool", "query", "status", "text", "stats", "latency_ms"} /
  {"type": "token", "text"} / {"type": "final", "text"}
引用由工具执行时写入外部传入的 citations 列表，编排结束后由调用方读取。

工具统计（命中数 / 实体数 / 网页数 等）由各工具写入 ctx.stats_holder[tool_key]，
本编排器在产 tool_result 事件时读取并附在事件上，前端 chip 副文动态绑定。
"""
import ast
import asyncio
import json
import re
import time
from collections.abc import AsyncGenerator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from app.core.agent.prompt_renderer import render_agent_prompt
from app.config import settings
from app.core.agent.runtime import ExecutionState, execute_tool, fit_context
from app.core.agent.tracing import get_tracer
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_TOOL_ITERATIONS = 5
MAX_TOOL_RESULT_PREVIEW = 600
FINAL_ANSWER_TIMEOUT_SECONDS = 45


def _format_observation(observation: object) -> str:
    """把工具返回值格式化为人类与 LLM 都能读的文本。

    设计目标：
    - MCP 工具常返回 ``[{'type': 'text', 'text': '...'}]``（或其字符串形式），
      抽出 text 字段拼接，避免出现一坨 Python 字面量噪声。
    - 普通 dict / list 用 JSON 美化输出，保留结构感。
    - 字符串原样返回；若它本身是 Python 字面量字符串（容器），尝试 literal_eval 后递归格式化。
    - 任意对象优先取 ``text`` 属性（兼容 mcp.types.TextContent 等）。
    """
    # 字符串：先看是不是 Python 字面量序列化的形式（如 "[{'type': 'text', ...}]"）
    if isinstance(observation, str):
        text = observation.strip()
        if text and text[0] in "[{(" and text[-1] in "]})":
            try:
                parsed = ast.literal_eval(text)
                if not isinstance(parsed, str):
                    return _format_observation(parsed)
            except (ValueError, SyntaxError):
                pass
        return text

    # 列表：典型 MCP 多段内容；逐项抽 text，否则降级到 str
    if isinstance(observation, list):
        parts: list[str] = []
        for item in observation:
            if isinstance(item, dict):
                t = item.get("text") if isinstance(item.get("text"), str) else None
                if t is not None:
                    parts.append(t)
                    continue
            attr = getattr(item, "text", None)
            if isinstance(attr, str):
                parts.append(attr)
                continue
            try:
                parts.append(json.dumps(item, ensure_ascii=False, indent=2))
            except (TypeError, ValueError):
                parts.append(str(item))
        return "\n\n".join(p.strip() for p in parts if p)

    # 字典：优先 text 字段，否则 JSON 美化
    if isinstance(observation, dict):
        t = observation.get("text") if isinstance(observation.get("text"), str) else None
        if t is not None:
            return t
        try:
            return json.dumps(observation, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(observation)

    # 其他对象（如 mcp.types.TextContent）：尝试 text 属性
    attr = getattr(observation, "text", None)
    if isinstance(attr, str):
        return attr
    return str(observation)


def _truncate(text: str) -> str:
    """前端展示预览用：截断到 MAX_TOOL_RESULT_PREVIEW 长度。"""
    if len(text) <= MAX_TOOL_RESULT_PREVIEW:
        return text
    return text[:MAX_TOOL_RESULT_PREVIEW].rstrip() + "..."


async def _finalize_without_tools(
    model: ChatOpenAI,
    messages: list,
    *,
    model_name: str,
) -> AsyncGenerator[dict, None]:
    """After the tool budget is exhausted, ask once for a bounded final answer.

    Intermediate assistant text from tool-selection rounds is intentionally not
    streamed to the client. This helper runs with no tools bound, so the model
    can only synthesize from the conversation and tool observations already in
    ``messages``.
    """
    final_messages = list(messages)
    final_instruction = SystemMessage(
        content=(
            "工具调用次数已达到上限。现在请只根据当前对话和已经获得的工具结果，"
            "给出完整的最终答复；不要再请求工具。若现有证据不足，请明确说明不确定之处。"
        )
    )
    insert_at = 1 if final_messages and isinstance(final_messages[0], SystemMessage) else 0
    final_messages.insert(insert_at, final_instruction)

    fit_context(final_messages)
    tracer = get_tracer()
    final_model = model.bind(max_tokens=settings.agent_output_token_budget)
    answer = ""
    last_chunk = None
    async with tracer.llm_span(
        f"chat:{model_name} (final-only)",
        model_name=model_name,
        attributes={"zhiyi.chat.final_only": True},
    ) as span:
        span.set_payload("messages_count", len(final_messages))
        async with asyncio.timeout(FINAL_ANSWER_TIMEOUT_SECONDS):
            async for chunk in final_model.astream(final_messages):
                last_chunk = chunk
                if chunk.content:
                    text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                    answer += text
                    yield {"type": "token", "text": text}
        usage = getattr(last_chunk, "usage_metadata", None) or {}
        span.set_tokens(
            input=int(usage.get("input_tokens", 0) or 0),
            output=int(usage.get("output_tokens", 0) or 0),
            model_name=model_name,
        )
        if answer:
            span.set_payload("response_preview", answer[:600])

    answer = answer.strip()
    if not answer:
        answer = "基于已获得的信息，暂时无法生成完整回答。"
        yield {"type": "token", "text": answer}
    yield {"type": "final", "text": answer}


async def run_function_calling(
    model: ChatOpenAI,
    tools: list[StructuredTool],
    messages: list,
    stats_holder: dict[str, dict] | None = None,
) -> AsyncGenerator[dict, None]:
    """强模型路径：原生 function calling 工具循环。

    每轮先缓存模型文本，只有确认没有工具调用时才把它作为最终答案发出，
    避免展示工具调用轮里的中间内容。
    """
    tool_map = {t.name: t for t in tools}
    model_with_tools = (model.bind_tools(tools) if tools else model).bind(
        max_tokens=settings.agent_output_token_budget
    )
    stats_holder = stats_holder if stats_holder is not None else {}
    # 同一轮内的工具调用结果缓存：(工具名+参数) 相同则复用上次结果，
    # 不再重复握手+执行（消除模型用相同参数重复调同一工具的浪费）。
    call_cache: dict[str, tuple[str, str]] = {}
    # 取真实 model_name 用于成本核算(LangChain ChatOpenAI 的 model_name 字段)
    chat_model_name = getattr(model, "model_name", None) or getattr(model, "model", "chat")
    tracer = get_tracer()

    execution = ExecutionState()
    for iteration in range(MAX_TOOL_ITERATIONS):
        fit_context(messages, tools)
        if execution.cached_repeats >= 2:
            break
        # 每轮 LLM 流式调用包一个 llm_call span,流完后从 usage_metadata 抽 token
        # 抓最后一条 user/tool 消息做请求摘要
        last_msg_text = ""
        for m in reversed(messages):
            content = getattr(m, "content", None)
            if isinstance(content, str) and content:
                last_msg_text = content
                break
        async with tracer.llm_span(
            f"chat:{chat_model_name} (轮 {iteration + 1})",
            model_name=chat_model_name,
            attributes={
                "zhiyi.chat.iteration": iteration + 1,
                "zhiyi.chat.tools_bound": len(tools),
            },
        ) as lsp:
            lsp.set_payload("messages_count", len(messages))
            if last_msg_text:
                lsp.set_payload("request_summary", last_msg_text[:600])
            gathered = None
            iter_text = ""
            async with asyncio.timeout(settings.agent_model_timeout_seconds):
                async for chunk in model_with_tools.astream(messages):
                    if chunk.content:
                        text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                        iter_text += text
                    gathered = chunk if gathered is None else gathered + chunk
            # 抽 token 用量(stream_usage=True 后流尾的 chunk 带 usage_metadata)
            usage = getattr(gathered, "usage_metadata", None) or {}
            in_t = int(usage.get("input_tokens", 0) or 0)
            out_t = int(usage.get("output_tokens", 0) or 0)
            cached = int((usage.get("input_token_details") or {}).get("cache_read", 0) or 0)
            lsp.set_tokens(input=in_t, output=out_t, cached=cached, model_name=chat_model_name)
            tool_calls = getattr(gathered, "tool_calls", None) or []
            lsp.set_payload("tool_calls_count", len(tool_calls))
            if iter_text:
                lsp.set_payload("response_preview", iter_text[:600])
            elif tool_calls:
                # 没有文本输出但触发了工具:展示工具调用意图
                lsp.set_payload(
                    "response_preview",
                    "(本轮无文字输出,触发工具:" + ", ".join(tc.get("name", "?") for tc in tool_calls[:5]) + ")",
                )

        if not tool_calls:
            # 无工具调用 → 已是最终回答
            if iter_text:
                yield {"type": "token", "text": iter_text}
            yield {"type": "final", "text": iter_text}
            return

        if execution.tool_calls + len(tool_calls) > settings.agent_max_tool_calls:
            execution.stop_reason = "tool_call_budget"
            break
        # 有工具调用：执行后把结果回灌，继续循环
        messages.append(gathered)
        for tc in tool_calls:
            execution.tool_calls += 1
            name = tc.get("name", "")
            args = tc.get("args", {}) or {}
            query = args.get("query", "") if isinstance(args, dict) else ""
            yield {"type": "tool_start", "tool": name, "query": query}
            try:
                cache_key = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
            except (TypeError, ValueError):
                cache_key = f"{name}:{args}"
            tool = tool_map.get(name)
            status = "success"
            t0 = time.monotonic()
            cached = call_cache.get(cache_key)
            if cached is not None:
                execution.cached_repeats += 1
                # 命中本轮缓存：相同工具+参数已调用过，直接复用，跳过握手与执行
                formatted, cached_status = cached
                latency_ms = 0
                stats: dict = {}
                yield {
                    "type": "tool_result",
                    "tool": name,
                    "query": query,
                    "status": cached_status,
                    "text": _truncate(formatted),
                    "stats": stats,
                    "latency_ms": latency_ms,
                    "cached": True,
                }
                messages.append(
                    ToolMessage(content=formatted, tool_call_id=tc.get("id", name))
                )
                continue
            if tool is None:
                observation = f"未知工具：{name}"
                status = "error"
            else:
                tracer = get_tracer()
                async with tracer.span(
                    f"工具:{name}",
                    span_type="tool_call",
                    attributes={
                        "zhiyi.tool.name": name,
                        "zhiyi.tool.query": str(query)[:200],
                    },
                ) as tsp:
                    try:
                        observation, status = await execute_tool(tool, args, name)
                    except Exception as e:
                        observation = f"工具执行失败：{type(e).__name__}"
                        status = "error"
                        tsp.mark_error(type(e).__name__)
                    obs_str = str(observation) if observation else ""
                    tsp.set_payload("status", status)
                    if status == "error":
                        tsp.mark_error(obs_str[:300])
                    tsp.set_payload("output_chars", len(obs_str))
                    if obs_str:
                        # 工具返回内容前 600 字预览,供 trace 详情面板展示
                        tsp.set_payload("output_preview", obs_str[:600])
                    if query:
                        tsp.set_payload("tool_query", str(query)[:300])
            latency_ms = int((time.monotonic() - t0) * 1000)
            stats = stats_holder.pop(name, {}) if stats_holder is not None else {}
            formatted = _format_observation(observation)
            if status in {"success", "empty"}:
                call_cache[cache_key] = (formatted, status)
            yield {
                "type": "tool_result",
                "tool": name,
                "query": query,
                "status": status,
                "text": _truncate(formatted),
                "stats": stats,
                "latency_ms": latency_ms,
            }
            messages.append(
                ToolMessage(content=formatted, tool_call_id=tc.get("id", name))
            )

    # 工具轮数耗尽后，用一次禁用工具的调用整合现有证据。
    async for event in _finalize_without_tools(
        model, messages, model_name=chat_model_name
    ):
        yield event


_ACTION_RE = re.compile(r"Action\s*:\s*(.+)")
_ACTION_INPUT_RE = re.compile(r"Action\s*Input\s*:\s*(.+)")
_FINAL_RE = re.compile(r"Final\s*Answer\s*:\s*(.*)", re.DOTALL)


async def run_react(
    model: ChatOpenAI,
    tools: list[StructuredTool],
    user_text: str,
    history: list,
    system_prompt: str,
    stats_holder: dict[str, dict] | None = None,
) -> AsyncGenerator[dict, None]:
    """弱模型路径：prompt 模拟 ReAct，手动解析并调用工具。"""
    tool_map = {t.name: t for t in tools}
    sys = render_agent_prompt(
        "react.jinja2",
        tools=[{"name": t.name, "description": t.description} for t in tools],
        system_prompt=system_prompt,
    )
    convo: list = [SystemMessage(content=sys), *history, HumanMessage(content=user_text)]
    stats_holder = stats_holder if stats_holder is not None else {}
    # 同一轮内工具结果缓存（工具名+query 相同则复用）
    call_cache: dict[str, tuple[str, str]] = {}
    # 取真实 model_name 用于 token / cost 记账
    react_model_name = getattr(model, "model_name", None) or getattr(model, "model", "chat")
    tracer = get_tracer()

    execution = ExecutionState()
    for iteration in range(MAX_TOOL_ITERATIONS):
        fit_context(convo, tools)
        if execution.cached_repeats >= 2:
            break
        if execution.tool_calls >= settings.agent_max_tool_calls:
            break
        async with tracer.llm_span(
            f"chat(ReAct):{react_model_name} (轮 {iteration + 1})",
            model_name=react_model_name,
            attributes={
                "zhiyi.chat.iteration": iteration + 1,
                "zhiyi.chat.mode": "react",
            },
        ) as lsp:
            async with asyncio.timeout(settings.agent_model_timeout_seconds):
                resp = await model.bind(max_tokens=settings.agent_output_token_budget).ainvoke(convo)
            usage = getattr(resp, "usage_metadata", None) or {}
            in_t = int(usage.get("input_tokens", 0) or 0)
            out_t = int(usage.get("output_tokens", 0) or 0)
            cached = int((usage.get("input_token_details") or {}).get("cache_read", 0) or 0)
            lsp.set_tokens(input=in_t, output=out_t, cached=cached, model_name=react_model_name)
        text = resp.content if isinstance(resp.content, str) else str(resp.content)

        final_match = _FINAL_RE.search(text)
        if final_match:
            answer = final_match.group(1).strip()
            yield {"type": "token", "text": answer}
            yield {"type": "final", "text": answer}
            return

        action_match = _ACTION_RE.search(text)
        input_match = _ACTION_INPUT_RE.search(text)
        if not action_match:
            # 没有 Action 也没有 Final，把整段当回答兜底
            yield {"type": "token", "text": text}
            yield {"type": "final", "text": text}
            return

        execution.tool_calls += 1
        tool_name = action_match.group(1).strip().splitlines()[0].strip()
        query = (input_match.group(1).strip().splitlines()[0].strip() if input_match else user_text)
        yield {"type": "tool_start", "tool": tool_name, "query": query}

        cache_key = f"{tool_name}:{query}"
        tool = tool_map.get(tool_name)
        status = "success"
        t0 = time.monotonic()
        cached = call_cache.get(cache_key)
        if cached is not None:
            execution.cached_repeats += 1
            formatted, cached_status = cached
            yield {
                "type": "tool_result",
                "tool": tool_name,
                "query": query,
                "status": cached_status,
                "text": _truncate(formatted),
                "stats": {},
                "latency_ms": 0,
                "cached": True,
            }
            convo.append(AIMessage(content=text))
            convo.append(HumanMessage(content=f"Observation: {formatted}"))
            continue
        if tool is None:
            observation = f"未知工具：{tool_name}"
            status = "error"
        else:
            tracer = get_tracer()
            async with tracer.span(
                f"工具:{tool_name}",
                span_type="tool_call",
                attributes={
                    "zhiyi.tool.name": tool_name,
                    "zhiyi.tool.query": str(query)[:200],
                },
            ) as tsp:
                try:
                    observation, status = await execute_tool(tool, {"query": query}, tool_name)
                except Exception as e:
                    observation = f"工具执行失败：{type(e).__name__}"
                    status = "error"
                    tsp.mark_error(type(e).__name__)
                obs_str = str(observation) if observation else ""
                tsp.set_payload("status", status)
                if status == "error":
                    tsp.mark_error(obs_str[:300])
                tsp.set_payload("output_chars", len(obs_str))
                if obs_str:
                    tsp.set_payload("output_preview", obs_str[:600])
                if query:
                    tsp.set_payload("tool_query", str(query)[:300])
        latency_ms = int((time.monotonic() - t0) * 1000)
        stats = stats_holder.pop(tool_name, {}) if stats_holder is not None else {}
        formatted = _format_observation(observation)
        if status in {"success", "empty"}:
            call_cache[cache_key] = (formatted, status)
        yield {
            "type": "tool_result",
            "tool": tool_name,
            "query": query,
            "status": status,
            "text": _truncate(formatted),
            "stats": stats,
            "latency_ms": latency_ms,
        }
        # 把模型上一轮输出 + Observation 回灌
        convo.append(AIMessage(content=text))
        convo.append(HumanMessage(content=f"Observation: {formatted}"))

    # ReAct 路径也必须在上限处生成可供 ChatService 保存的最终答复。
    async for event in _finalize_without_tools(
        model, convo, model_name=react_model_name
    ):
        yield event


__all__ = ["run_function_calling", "run_react", "MAX_TOOL_ITERATIONS"]
