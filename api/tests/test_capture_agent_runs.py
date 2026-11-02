import httpx
import pytest

from eval.capture_agent_runs import capture_case, parse_sse_block


def test_parse_sse_block_keeps_event_and_json_payload():
    parsed = parse_sse_block(["event: tool_result", 'data: {"tool":"knowledge_search","status":"error"}'])
    assert parsed == ("tool_result", {"tool": "knowledge_search", "status": "error"})
    assert parse_sse_block([": heartbeat"]) is None


def test_parse_sse_block_rejects_non_object_payload():
    with pytest.raises(ValueError, match="object"):
        parse_sse_block(["data: []"])


@pytest.mark.asyncio
async def test_capture_case_records_actual_sse_and_disables_memory_for_rag():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/conversations":
            return httpx.Response(200, json={"code": 0, "data": {"id": "conversation-1"}})
        return httpx.Response(200, text=(
            'event: meta\ndata: {"trace_id":"trace-1"}\n\n'
            'event: tool_start\ndata: {"tool":"knowledge_search"}\n\n'
            'event: token\ndata: {"text":"有据回答"}\n\n'
            'event: citation\ndata: {"citations":[{"evidence_id":"K1"}]}\n\n'
            'event: done\ndata: {"conversation_id":"conversation-1"}\n\n'
        ))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        result = await capture_case(
            client, {"case_id": "rag-001", "case_type": "rag", "question": "测试"}, "token"
        )
    assert result["answer"] == "有据回答"
    assert result["trace_id"] == "trace-1"
    assert result["called_tools"] == ["knowledge_search"]
    assert result["citations"] == [{"evidence_id": "K1"}]
    assert result["done"] and not result["errors"]
    assert requests[1].headers["authorization"] == "Bearer token"
    assert b'"enable_memory":false' in requests[1].content
