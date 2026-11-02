"""Small execution controls shared by the two existing agent loops."""

import asyncio
import json
from dataclasses import dataclass

import httpx
import jsonschema
import tiktoken
from pydantic import BaseModel, ValidationError

from app.config import settings

_encoder = tiktoken.get_encoding("cl100k_base")


class ContextBudgetExceeded(ValueError):
    pass


def token_count(messages, tools=()) -> int:
    # Provider-independent estimate, including message boundaries and tool schema.
    data = [
        {
            "role": getattr(m, "type", "unknown"),
            "content": getattr(m, "content", ""),
            "tool_calls": getattr(m, "tool_calls", None),
        }
        for m in messages
    ]
    schemas = [t.args_schema.model_json_schema() for t in tools if getattr(t, "args_schema", None)]
    return len(
        _encoder.encode(json.dumps([data, schemas], ensure_ascii=False, default=str))
    ) + 16 * len(messages)


def fit_context(messages, tools=()):
    """Drop complete oldest history/tool rounds; never discard current question/system.

    A tool-calling assistant and its results are removed as one group, so provider
    protocol is not broken by leaving dangling ToolMessages.
    """
    budget = settings.agent_input_token_budget
    while token_count(messages, tools) > budget:
        humans = [
            i
            for i, m in enumerate(messages)
            if getattr(m, "type", "") == "human"
            and not str(getattr(m, "content", "")).startswith("Observation:")
        ]
        current = humans[-1] if humans else -1
        if current > 1:
            # Remove old dialogue until the next human turn (not the current one).
            next_human = next((i for i in humans if i > 1), current)
            del messages[1:next_human]
        elif current >= 0 and current + 1 < len(messages) - 1:
            start = current + 1
            end = start + 1
            while end < len(messages) and (
                getattr(messages[end], "type", "") == "tool"
                or str(getattr(messages[end], "content", "")).startswith("Observation:")
            ):
                end += 1
            del messages[start:end]
        else:
            raise ContextBudgetExceeded("当前问题、系统规则和工具定义超过上下文预算，请缩短输入")
    return messages


@dataclass
class ExecutionState:
    tool_calls: int = 0
    cached_repeats: int = 0
    stop_reason: str | None = None


async def execute_tool(tool, args, name):
    """Validate model arguments, bound execution, and normalize tool outcomes.

    A tool's text is untrusted data. Only registered read tools get an automatic
    transport retry; an unknown/MCP tool may have side effects.
    """
    if not isinstance(args, dict):
        return "工具参数格式错误", "error"
    try:
        encoded_args = json.dumps(args, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "工具参数无法编码", "error"
    if len(encoded_args) > settings.agent_tool_argument_max_chars:
        return "工具参数超过长度限制", "error"
    schema = getattr(tool, "args_schema", None)
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        try:
            args = schema.model_validate(args).model_dump()
        except ValidationError:
            return "工具参数不符合声明的 Schema", "error"
    elif isinstance(schema, dict):
        try:
            jsonschema.Draft202012Validator(schema).validate(args)
        except (jsonschema.ValidationError, jsonschema.SchemaError):
            return "工具参数不符合声明的 Schema", "error"

    attempts = 1 + (
        settings.agent_tool_retries
        if name in {"knowledge_search", "memory_search", "web_search", "datetime"}
        else 0
    )
    for attempt in range(attempts):
        try:
            async with asyncio.timeout(settings.agent_tool_timeout_seconds):
                result = await tool.ainvoke(args)
            output = (
                result if isinstance(result, str)
                else json.dumps(result, ensure_ascii=False, default=str)
            )
            if len(output) > settings.agent_tool_output_max_chars:
                return "工具结果超过长度限制，请缩小查询范围", "error"
            if not output.strip():
                return "工具没有返回内容", "empty"
            structured = result if isinstance(result, dict) else None
            if structured is None and isinstance(result, str) and result.startswith("{"):
                try:
                    structured = json.loads(result)
                except json.JSONDecodeError:
                    pass
            if isinstance(structured, dict):
                declared = structured.get("status")
                if declared == "empty":
                    return result, "empty"
                if declared == "error":
                    return result, "error"
                if declared == "ok":
                    return result, "success"
            if isinstance(result, str) and result.startswith(("工具执行失败", "联网搜索失败", "ERROR:")):
                return result, "error"
            return result, "success"
        except (TimeoutError, ConnectionError, httpx.TransportError) as exc:
            if attempt + 1 < attempts:
                await asyncio.sleep(0.1 * (attempt + 1))
                continue
            return f"工具暂不可用（超时或连接失败）：{type(exc).__name__}", "error"
        except Exception as exc:
            # Validation, permission and unknown errors are not blindly retried.
            return f"工具执行失败：{type(exc).__name__}", "error"
