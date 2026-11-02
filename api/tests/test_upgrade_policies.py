"""Offline memory/runtime/verification acceptance cases. No external calls."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from app.config import settings
from app.core.agent.runtime import fit_context, token_count, execute_tool, ContextBudgetExceeded
from app.core.agent.verification import verify_answer, SAFE_ANSWER
from app.core.memory.governance.policy import decide, classify_kind, source_id, fact_slot, expiry
from app.core.memory.governance.service import prepare_policy
from app.core.memory.graph_models import StatementNode
from app.core.memory.preprocessing.statement_extractor import extract_statements


class Client:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    async def chat(self, *args, **kwargs):
        self.calls += 1
        result = self.answers.pop(0)
        if isinstance(result, Exception):
            raise result
        return json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else result


def decision(action, ids=(), confidence=0.98):
    return dict(action=action, target_ids=list(ids), confidence=confidence, reason="test")


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    def test_plan_not_fact(self):
        self.assertEqual(classify_kind("用户计划去北京", "FACT"), "PLAN")
        self.assertEqual(classify_kind("用户觉得这个不错", "OPINION"), "OPINION")

    def test_explicit_plan_date_is_not_shortened_to_default_ttl(self):
        from datetime import datetime
        now = datetime(2026, 11, 2)
        self.assertEqual(expiry("PLAN", now, "我计划六个月后毕业"),
                         datetime(2027, 5, 2).isoformat())

    def test_current_city_has_slot_but_future_plan_does_not(self):
        self.assertEqual(fact_slot("我现在在杭州工作", "FACT"), "user:current_work_city")
        self.assertIsNone(fact_slot("我计划去上海工作", "PLAN"))

    async def test_exact_duplicate_without_model(self):
        c = Client([])
        r = await decide(
            c,
            dict(statement="用户研究CSI。", memory_kind="FACT"),
            [dict(id="old", statement="用户研究CSI", memory_kind="FACT")],
        )
        self.assertEqual(r.action, "NOOP")
        self.assertEqual(c.calls, 0)

    async def test_same_text_different_kind_not_merged(self):
        r = await decide(
            Client([decision("ADD")]),
            dict(statement="去北京", memory_kind="PLAN"),
            [dict(id="old", statement="去北京", memory_kind="FACT")],
        )
        self.assertEqual(r.action, "ADD")

    async def test_unknown_target_rejected(self):
        r = await decide(
            Client([decision("UPDATE", ["other-user"])]),
            dict(statement="用户现在住深圳", memory_kind="FACT"),
            [dict(id="old", statement="用户住北京", memory_kind="FACT")],
        )
        self.assertEqual(r.action, "CONFLICT")
        self.assertEqual(r.target_ids, [])

    async def test_ambiguous_change_not_overwritten(self):
        r = await decide(
            Client([decision("UPDATE", ["old"])]),
            dict(statement="用户喜欢咖啡", memory_kind="FACT"),
            [dict(id="old", statement="用户喜欢茶", memory_kind="FACT")],
        )
        self.assertEqual(r.action, "CONFLICT")

    async def test_new_city_without_correction_requires_confirmation(self):
        r = await decide(
            Client([decision("UPDATE", ["old"])]),
            dict(statement="用户现在住在深圳", memory_kind="FACT",
                 slot_key="user:current_residence_city", source_text="我现在住在深圳"),
            [dict(id="old", statement="用户现在住在北京", memory_kind="FACT",
                  slot_key="user:current_residence_city")],
        )
        self.assertEqual(r.action, "CONFLICT")

    async def test_explicit_change_allowed(self):
        r = await decide(
            Client([decision("UPDATE", ["old"])]),
            dict(statement="用户现在住在深圳", memory_kind="FACT",
                 slot_key="user:current_residence_city",
                 source_text="我已经从北京搬到深圳居住"),
            [dict(id="old", statement="用户现在住在北京", memory_kind="FACT",
                  slot_key="user:current_residence_city")],
        )
        self.assertEqual(r.action, "UPDATE")

    async def test_model_cannot_add_second_current_work_city(self):
        r = await decide(
            Client([decision("ADD")]),
            dict(statement="我现在在深圳工作", memory_kind="FACT",
                 slot_key="user:current_work_city"),
            [dict(id="old", statement="我目前在杭州工作", memory_kind="FACT",
                  slot_key="user:current_work_city")],
        )
        self.assertEqual(r.action, "CONFLICT")
        self.assertEqual(r.target_ids, ["old"])

    async def test_same_slot_candidate_recalled_despite_low_vector_similarity(self):
        repo = SimpleNamespace(memory_policy_snapshot=AsyncMock(return_value=dict(
            revision=3, statements=[dict(id="old", statement="我目前在杭州工作",
                                         memory_kind="FACT", embedding=[-1, 0])]
        )))
        embed = SimpleNamespace(embed=AsyncMock(return_value=[[1, 0]]))
        client = Client([decision("ADD")])
        plan = await prepare_policy(repo, client, embed, "u", [
            StatementNode(user_id="u", chunk_id="c", statement="我现在在深圳工作")
        ], "source")
        self.assertEqual(plan["plans"][0]["action"], "CONFLICT")
        self.assertEqual(plan["plans"][0]["targets"], ["old"])

    async def test_judge_failure_pending(self):
        r = await decide(
            Client([RuntimeError("down")]),
            dict(statement="new", memory_kind="FACT"),
            [dict(id="old", statement="old", memory_kind="FACT")],
        )
        self.assertEqual(r.action, "CONFLICT")

    async def test_multi_fact_input_independent(self):
        repo = SimpleNamespace(
            memory_policy_snapshot=AsyncMock(return_value=dict(revision=0, statements=[]))
        )
        embed = SimpleNamespace(embed=AsyncMock(return_value=[[1, 0], [0, 1]]))
        statements = [
            StatementNode(user_id="u", chunk_id="c", statement="用户研究CSI"),
            StatementNode(user_id="u", chunk_id="c", statement="用户计划去北京"),
        ]
        plan = await prepare_policy(
            repo, Client([decision("ADD")]), embed, "u", statements, "ingest"
        )
        self.assertEqual([p["memory_kind"] for p in plan["plans"]], ["FACT", "PLAN"])
        self.assertIsNotNone(plan["plans"][1]["expires_at"])

    async def test_extraction_error_not_empty(self):
        with self.assertRaises(RuntimeError):
            await extract_statements(Client([RuntimeError("down")]), "研究CSI")

    def test_stable_user_scoped_ids(self):
        self.assertEqual(source_id("u", "task", "s"), source_id("u", "task", "s"))
        self.assertNotEqual(source_id("u", "task", "s"), source_id("v", "task", "s"))


class SourceValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_invented_id_cannot_be_approved(self):
        from app.core.memory.governance.policy import validate_sources

        c = Client([dict(supported_ids=["invented"], reason="bad")])
        self.assertEqual(
            await validate_sources(c, "我研究CSI", [dict(id="s1", statement="用户研究CSI")]), set()
        )

    async def test_source_failure_does_not_admit_fact(self):
        from app.core.memory.governance.policy import validate_sources

        self.assertEqual(
            await validate_sources(
                Client([RuntimeError("down")]), "text", [dict(id="s1", statement="a")]
            ),
            set(),
        )

    def test_plan_format_remains_plan(self):
        from app.core.memory.retrieval.searcher import format_memory_context

        text = format_memory_context(
            [
                dict(
                    name="用户",
                    type="记忆",
                    confidence=0.9,
                    statements=[dict(statement="用户计划旅行", memory_kind="PLAN", confidence=0.9)],
                )
            ]
        )
        self.assertIn("计划：用户计划旅行", text)
        self.assertNotIn("事实：用户计划旅行", text)

    def test_historical_event_separate_kind(self):
        self.assertEqual(classify_kind("用户上周完成了项目上线", "FACT", "STATIC"), "EVENT")


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_external_tools_require_explicit_allowlist(self):
        from app.core.agent.tools.registry import _allowed_external_tools

        tools = [SimpleNamespace(name="trusted__read"), SimpleNamespace(name="unknown__write")]
        with patch.object(settings, "agent_external_tools_enabled", False):
            self.assertEqual(_allowed_external_tools(tools), [])
        with (
            patch.object(settings, "agent_external_tools_enabled", True),
            patch.object(settings, "agent_external_tool_allowlist", "trusted__read"),
        ):
            self.assertEqual([t.name for t in _allowed_external_tools(tools)], ["trusted__read"])

    def test_trim_history_keeps_question(self):
        messages = [
            SystemMessage("rules"),
            HumanMessage("old " * 1500),
            AIMessage("reply " * 1500),
            HumanMessage("current"),
        ]
        with patch.object(settings, "agent_input_token_budget", 150):
            fit_context(messages)
            self.assertLessEqual(token_count(messages), 150)
        self.assertEqual(messages[-1].content, "current")
        self.assertEqual(messages[0].content, "rules")

    def test_trim_complete_tool_group(self):
        messages = [
            SystemMessage("rules"),
            HumanMessage("current"),
            AIMessage(content="", tool_calls=[dict(name="x", args={}, id="c")]),
            ToolMessage(content="long " * 2500, tool_call_id="c"),
            AIMessage("brief"),
        ]
        with patch.object(settings, "agent_input_token_budget", 180):
            fit_context(messages)
        self.assertFalse(any(isinstance(m, ToolMessage) for m in messages))

    def test_current_input_too_large(self):
        with patch.object(settings, "agent_input_token_budget", 50):
            with self.assertRaises(ContextBudgetExceeded):
                fit_context([SystemMessage("rules"), HumanMessage("x " * 1000)])

    async def test_timeout_finite_retry(self):
        async def slow(args):
            await asyncio.sleep(1)

        t = SimpleNamespace(ainvoke=AsyncMock(side_effect=slow))
        with (
            patch.object(settings, "agent_tool_timeout_seconds", 0.005),
            patch.object(settings, "agent_tool_retries", 1),
        ):
            _, status = await execute_tool(t, {}, "knowledge_search")
        self.assertEqual(status, "error")
        self.assertEqual(t.ainvoke.await_count, 2)

    async def test_validation_error_no_retry(self):
        t = SimpleNamespace(ainvoke=AsyncMock(side_effect=ValueError("schema")))
        _, status = await execute_tool(t, {}, "knowledge_search")
        self.assertEqual(status, "error")
        self.assertEqual(t.ainvoke.await_count, 1)

    async def test_external_write_not_retried(self):
        t = SimpleNamespace(ainvoke=AsyncMock(side_effect=ConnectionError("down")))
        await execute_tool(t, {}, "external_write")
        self.assertEqual(t.ainvoke.await_count, 1)

    async def test_model_cannot_add_undeclared_tool_parameter(self):
        from app.core.agent.tools.builtin.knowledge import _QueryInput

        t = SimpleNamespace(
            args_schema=_QueryInput,
            ainvoke=AsyncMock(return_value='{"status":"ok","evidence":[]}'),
        )
        output, status = await execute_tool(
            t, {"query": "WiFi CSI", "user_id": "another-user"}, "knowledge_search"
        )
        self.assertEqual(status, "error")
        self.assertIn("Schema", output)
        t.ainvoke.assert_not_awaited()

    async def test_external_json_schema_rejects_wrong_parameter_type(self):
        t = SimpleNamespace(
            args_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "required": ["limit"],
                "additionalProperties": False,
            },
            ainvoke=AsyncMock(return_value="ok"),
        )
        _, status = await execute_tool(t, {"limit": "all"}, "external_read")
        self.assertEqual(status, "error")
        t.ainvoke.assert_not_awaited()

    async def test_structured_empty_and_error_are_not_success(self):
        t = SimpleNamespace(ainvoke=AsyncMock(return_value='{"status":"empty","evidence":[]}'))
        _, status = await execute_tool(t, {"query": "x"}, "knowledge_search")
        self.assertEqual(status, "empty")
        t.ainvoke.return_value = '{"status":"error","message":"unavailable"}'
        _, status = await execute_tool(t, {"query": "x"}, "knowledge_search")
        self.assertEqual(status, "error")

    async def test_large_tool_result_is_not_injected_into_model_context(self):
        t = SimpleNamespace(ainvoke=AsyncMock(return_value="private " * 50))
        with patch.object(settings, "agent_tool_output_max_chars", 30):
            output, status = await execute_tool(t, {}, "knowledge_search")
        self.assertEqual(status, "error")
        self.assertNotIn("private", output)


class VerifyTests(unittest.IsolatedAsyncioTestCase):
    evidence = [dict(evidence_id="K1", source_id="doc", content="答案42")]

    async def test_no_evidence_refusal(self):
        c = Client([])
        r = await verify_answer(c, "q", "invented", [], required=True)
        self.assertEqual(r.text, SAFE_ANSWER)
        self.assertEqual(c.calls, 0)

    async def test_known_source_passes(self):
        c = Client([dict(supported=True, evidence_ids=["K1"], reason="supported")])
        r = await verify_answer(c, "q", "42", self.evidence, required=True)
        self.assertEqual(r.status, "verified")

    async def test_fake_citation_rejected(self):
        c = Client([dict(supported=True, evidence_ids=["FAKE"], reason="bad")])
        r = await verify_answer(c, "q", "42", self.evidence, required=True, repair_limit=0)
        self.assertEqual(r.text, SAFE_ANSWER)

    async def test_repair_reverified(self):
        c = Client(
            [
                dict(supported=False, evidence_ids=[], reason="wrong"),
                "42",
                dict(supported=True, evidence_ids=["K1"], reason="fixed"),
            ]
        )
        r = await verify_answer(c, "q", "43", self.evidence, required=True)
        self.assertEqual(r.text, "42")
        self.assertEqual(r.attempts, 2)
        self.assertEqual(c.calls, 3)

    async def test_judge_failure_closed(self):
        r = await verify_answer(
            Client([RuntimeError("down")]), "q", "42", self.evidence, required=True
        )
        self.assertEqual(r.status, "verification_failed")

    async def test_general_chat(self):
        r = await verify_answer(None, "你好", "你好", [], required=False)
        self.assertEqual(r.status, "not_required")


class EvidencePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_obvious_private_question_requires_evidence_without_model_call(self):
        from app.core.agent.evidence_policy import needs_evidence

        model = SimpleNamespace(bind=AsyncMock())
        self.assertTrue(await needs_evidence(model, "我的科研方向是什么？", sources_available=True))
        model.bind.assert_not_called()

    async def test_ambiguous_question_is_classified_with_checked_schema(self):
        from app.core.agent.evidence_policy import needs_evidence

        model = SimpleNamespace(
            model_name="fake",
            ainvoke=AsyncMock(return_value=AIMessage(content='{"needs_private_evidence":false}')),
        )
        model.bind = lambda **_: model
        self.assertFalse(await needs_evidence(model, "什么是 TCP？", sources_available=True))

    async def test_classifier_failure_fails_closed(self):
        from app.core.agent.evidence_policy import needs_evidence

        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("down")))
        model.bind = lambda **_: model
        self.assertTrue(await needs_evidence(model, "研究方向是什么？", sources_available=True))


class ChatGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_citation_excerpt_is_sent_to_user_but_not_message_metadata(self):
        import uuid
        from app.services import chat_service as module
        from app.schemas.chat_schema import ChatStreamRequest

        service = module.ChatService(None)
        service._ensure_conversation = AsyncMock(
            return_value=SimpleNamespace(id=uuid.uuid4(), title="test")
        )
        service._history = AsyncMock(return_value=[])
        service._dispatch_memory = AsyncMock()
        saved = []

        async def add(message):
            message.id = uuid.uuid4()
            saved.append(message)
            return message

        service.messages = SimpleNamespace(add=add)
        service.conversations = SimpleNamespace(touch=AsyncMock())
        kb = SimpleNamespace(list_chat_enabled_ids=AsyncMock(return_value=[]))
        citations_holder = []

        async def fake_build(session, user_id, collector, *args, **kwargs):
            citations_holder.append(collector)
            return [object()]

        async def fake_run(*args, **kwargs):
            citations_holder[0].append({
                "evidence_id": "K1", "source_id": "doc-1", "source_type": "document",
                "doc_name": "notes.pdf", "chunk_id": "child-1", "content": "支撑答案的原文片段",
            })
            yield {"type": "token", "text": "候选回答"}

        verified = SimpleNamespace(
            text="有依据的回答", status="verified", evidence_ids=["K1"], attempts=1, reason="支持"
        )
        from app.core.llm import resolver
        with (
            patch.object(module, "KnowledgeBaseRepository", return_value=kb),
            patch.object(module, "build_enabled_tools", side_effect=fake_build),
            patch.object(module, "build_default_chat_model", AsyncMock(return_value=(object(), None))),
            patch.object(module, "supports_function_call", return_value=True),
            patch.object(module, "needs_evidence", AsyncMock(return_value=True)),
            patch.object(module, "run_function_calling", side_effect=fake_run),
            patch.object(module, "verify_answer", AsyncMock(return_value=verified)),
            patch.object(resolver, "get_optional_client_for_type", AsyncMock(return_value=object())),
        ):
            events = [e async for e in service._stream_chat(
                uuid.uuid4(), ChatStreamRequest(message="根据文档回答", enable_memory=False)
            )]

        self.assertIn("支撑答案的原文片段", "".join(events))
        self.assertNotIn("content", saved[-1].meta_data["citations"][0])
        self.assertIn('"evidence_id": "K1"', "".join(events))

    async def test_failed_tool_cannot_leave_citation_as_evidence(self):
        import uuid
        from app.services import chat_service as module
        from app.schemas.chat_schema import ChatStreamRequest

        service = module.ChatService(None)
        service._ensure_conversation = AsyncMock(
            return_value=SimpleNamespace(id=uuid.uuid4(), title="test")
        )
        service._history = AsyncMock(return_value=[])
        service._dispatch_memory = AsyncMock()
        saved = []

        async def add(message):
            message.id = uuid.uuid4()
            saved.append(message)
            return message

        async def fake_build(session, user_id, collector, *args, **kwargs):
            assert collector is not None
            # fake_run uses the same collector as ChatService.
            nonlocal_citations[0] = collector
            return [object()]

        nonlocal_citations = [None]

        async def fake_run_with_collector(*args, **kwargs):
            yield {"type": "tool_start", "tool": "knowledge_search", "query": "test"}
            nonlocal_citations[0].append({"evidence_id": "bad-1", "content": "unusable source"})
            yield {"type": "tool_result", "tool": "knowledge_search", "status": "error"}
            yield {"type": "token", "text": "UNSUPPORTED_CANARY"}

        service.messages = SimpleNamespace(add=add)
        service.conversations = SimpleNamespace(touch=AsyncMock())
        kb = SimpleNamespace(list_chat_enabled_ids=AsyncMock(return_value=[]))
        with (
            patch.object(module, "KnowledgeBaseRepository", return_value=kb),
            patch.object(module, "build_enabled_tools", side_effect=fake_build),
            patch.object(module, "build_default_chat_model", AsyncMock(return_value=(object(), None))),
            patch.object(module, "supports_function_call", return_value=True),
            patch.object(module, "run_function_calling", side_effect=fake_run_with_collector),
        ):
            events = [
                e async for e in service._stream_chat(
                    uuid.uuid4(), ChatStreamRequest(message="根据文档回答", enable_memory=False)
                )
            ]
        self.assertNotIn("UNSUPPORTED_CANARY", "".join(events))
        self.assertEqual(saved[-1].content, SAFE_ANSWER)
        self.assertEqual(saved[-1].meta_data["citations"], [])

    async def test_unverified_candidate_not_streamed_or_saved(self):
        import uuid
        from app.services import chat_service as module
        from app.schemas.chat_schema import ChatStreamRequest

        class Model:
            def bind(self, **kwargs):
                return self

            async def astream(self, messages):
                yield SimpleNamespace(content="UNSUPPORTED_CANARY")

        service = module.ChatService(None)
        service._ensure_conversation = AsyncMock(
            return_value=SimpleNamespace(id=uuid.uuid4(), title="test")
        )
        service._history = AsyncMock(return_value=[])
        service._dispatch_memory = AsyncMock()
        saved = []

        async def add(m):
            m.id = uuid.uuid4()
            saved.append(m)
            return m

        service.messages = SimpleNamespace(add=add)
        service.conversations = SimpleNamespace(touch=AsyncMock())
        kb = SimpleNamespace(list_chat_enabled_ids=AsyncMock(return_value=[]))
        with (
            patch.object(module, "KnowledgeBaseRepository", return_value=kb),
            patch.object(module, "build_enabled_tools", AsyncMock(return_value=[])),
            patch.object(
                module, "build_default_chat_model", AsyncMock(return_value=(Model(), None))
            ),
        ):
            events = [
                e
                async for e in service._stream_chat(
                    uuid.uuid4(), ChatStreamRequest(message="根据文档回答", enable_memory=False)
                )
            ]
        self.assertNotIn("UNSUPPORTED_CANARY", "".join(events))
        self.assertEqual(saved[-1].content, SAFE_ANSWER)
