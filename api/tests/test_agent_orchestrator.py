import unittest
from unittest.mock import patch

from app.core.agent import orchestrator


class FakeSpan:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def set_payload(self, *args, **kwargs):
        pass

    def set_tokens(self, **kwargs):
        pass

    def mark_error(self, *args, **kwargs):
        pass


class FakeTracer:
    def llm_span(self, *args, **kwargs):
        return FakeSpan()

    def span(self, *args, **kwargs):
        return FakeSpan()


class FakeChunk:
    def __init__(self, content="", tool_calls=None, usage_metadata=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage_metadata = usage_metadata or {}

    def __add__(self, other):
        return FakeChunk(
            content=f"{self.content}{other.content}",
            tool_calls=self.tool_calls + other.tool_calls,
            usage_metadata=other.usage_metadata or self.usage_metadata,
        )


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {}


class FakeModel:
    model_name = "fake-model"

    def __init__(self, stream_rounds=None, invoke_rounds=None):
        self.stream_rounds = list(stream_rounds or [])
        self.invoke_rounds = list(invoke_rounds or [])
        self.bind_calls = []

    def bind_tools(self, tools):
        return self

    def bind(self, **kwargs):
        self.bind_calls.append(kwargs)
        return self

    async def astream(self, messages):
        chunks = self.stream_rounds.pop(0)
        for chunk in chunks:
            yield chunk

    async def ainvoke(self, messages):
        return self.invoke_rounds.pop(0)


class FakeTool:
    name = "knowledge_search"
    description = "Search indexed knowledge."

    def __init__(self, result="evidence", error=None):
        self.result = result
        self.error = error
        self.calls = 0

    async def ainvoke(self, args):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def tool_call(call_id="call-1", query="wifi csi"):
    return {
        "name": "knowledge_search",
        "args": {"query": query},
        "id": call_id,
    }


class AgentOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, events):
        return [event async for event in events]

    async def test_function_call_text_from_tool_round_is_not_streamed_as_answer(self):
        model = FakeModel(
            stream_rounds=[
                [FakeChunk("正在查询……"), FakeChunk(tool_calls=[tool_call()])],
                [FakeChunk("根据资料，答案是 42。")],
            ]
        )
        tool = FakeTool()

        with patch.object(orchestrator, "get_tracer", return_value=FakeTracer()):
            events = await self.collect(
                orchestrator.run_function_calling(model, [tool], [])
            )

        self.assertEqual(
            [event["text"] for event in events if event["type"] == "token"],
            ["根据资料，答案是 42。"],
        )
        self.assertEqual(tool.calls, 1)
        self.assertEqual(events[-1], {"type": "final", "text": "根据资料，答案是 42。"})

    async def test_function_call_budget_uses_final_only_call(self):
        model = FakeModel(
            stream_rounds=[
                [FakeChunk(tool_calls=[tool_call("call-1")])],
                [FakeChunk(tool_calls=[tool_call("call-2", "CSI signal processing")])],
                [FakeChunk("已根据现有证据整理结论。")],
            ]
        )
        tool = FakeTool()

        with (
            patch.object(orchestrator, "get_tracer", return_value=FakeTracer()),
            patch.object(orchestrator, "MAX_TOOL_ITERATIONS", 2),
        ):
            events = await self.collect(
                orchestrator.run_function_calling(model, [tool], [])
            )

        self.assertEqual(tool.calls, 2)
        self.assertEqual(
            model.bind_calls,
            [{"max_tokens": orchestrator.settings.agent_output_token_budget}] * 2,
        )
        self.assertEqual(
            [event["text"] for event in events if event["type"] == "token"],
            ["已根据现有证据整理结论。"],
        )
        self.assertEqual(events[-1]["type"], "final")

    async def test_tool_error_is_returned_as_observation_then_final_answer(self):
        model = FakeModel(
            stream_rounds=[
                [FakeChunk(tool_calls=[tool_call()])],
                [FakeChunk("检索暂时失败，我无法依据资料确认。")],
            ]
        )
        tool = FakeTool(error=RuntimeError("ES unavailable"))

        with patch.object(orchestrator, "get_tracer", return_value=FakeTracer()):
            events = await self.collect(
                orchestrator.run_function_calling(model, [tool], [])
            )

        result = next(event for event in events if event["type"] == "tool_result")
        self.assertEqual(result["status"], "error")
        self.assertIn("RuntimeError", result["text"])
        self.assertNotIn("ES unavailable", result["text"])
        self.assertEqual(events[-1]["type"], "final")

    async def test_react_budget_also_gets_a_final_only_answer(self):
        action = "Action: knowledge_search\nAction Input: wifi csi"
        model = FakeModel(
            invoke_rounds=[FakeResponse(action), FakeResponse(action)],
            stream_rounds=[[FakeChunk("這是基於前兩次工具結果的總結。")]],
        )
        tool = FakeTool()

        with (
            patch.object(orchestrator, "get_tracer", return_value=FakeTracer()),
            patch.object(orchestrator, "MAX_TOOL_ITERATIONS", 2),
            patch.object(orchestrator, "render_agent_prompt", return_value="system"),
        ):
            events = await self.collect(
                orchestrator.run_react(model, [tool], "問題", [], "规则")
            )

        self.assertEqual(tool.calls, 1)  # 同一请求的重复工具调用复用缓存。
        self.assertEqual(
            [event["text"] for event in events if event["type"] == "token"],
            ["這是基於前兩次工具結果的總結。"],
        )
        self.assertEqual(events[-1]["type"], "final")


if __name__ == "__main__":
    unittest.main()
