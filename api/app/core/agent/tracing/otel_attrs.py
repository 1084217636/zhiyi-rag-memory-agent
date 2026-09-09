"""OpenTelemetry GenAI semantic convention 属性常量。

参考:https://opentelemetry.io/docs/specs/semconv/gen-ai/

字段命名遵循标准,未来要导出 OTel Collector / Jaeger / Tempo 时零成本。
不自造土字段(如 `our_model`/`my_tokens`),所有 LLM 相关属性都用 `gen_ai.*` 前缀。
"""
from __future__ import annotations

# 通用 GenAI 属性
GEN_AI_SYSTEM = "gen_ai.system"  # 如 "openai" / "deepseek" / "zhipu" / "qwen"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"  # 如 "chat" / "embeddings" / "tool"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"  # 如 "deepseek-v4-pro"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"  # 实际返回的 model(provider 可能 alias)
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"

# Token 用量
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_CACHED_TOKENS = "gen_ai.usage.cached_tokens"  # OTel 0.30+ 新增

# 工具调用相关(tool_call / mcp_call span)
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_TYPE = "gen_ai.tool.type"  # function / mcp / builtin
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"

# Embedding 专用
GEN_AI_EMBEDDING_DIMENSIONS = "gen_ai.embedding.dimensions"

# 业务扩展属性(zhiyi.* 前缀,与标准 gen_ai.* 区分,未来易剥离)
ZHIYI_RETRIEVAL_TOP_K = "zhiyi.retrieval.top_k"
ZHIYI_RETRIEVAL_HIT_COUNT = "zhiyi.retrieval.hit_count"
ZHIYI_VERIFIER_KIND = "zhiyi.verifier.kind"  # same / cross
ZHIYI_VERIFIER_RUBRIC = "zhiyi.verifier.rubric"  # research / task
ZHIYI_LOOP_ITERATION_NO = "zhiyi.loop.iteration_no"
ZHIYI_REPAIR_ACTION = "zhiyi.repair.action"  # patch / rewrite
ZHIYI_TASK_TYPE = "zhiyi.task.type"  # research / chat / agent_task


# provider → gen_ai.system 名映射(对齐 OTel 命名)
PROVIDER_TO_SYSTEM = {
    "openai": "openai",
    "deepseek": "deepseek",
    "zhipu": "zhipu",
    "qwen": "qwen",
    "doubao": "doubao",
    "qianfan": "baidu",
    "tavily": "tavily",
}


def system_of(provider: str | None) -> str:
    """provider 名归一化为 OTel `gen_ai.system` 字段值。"""
    if not provider:
        return "unknown"
    return PROVIDER_TO_SYSTEM.get(provider.lower(), provider.lower())
