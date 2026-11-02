"""The PostgreSQL source row survives broker loss and duplicate deliveries."""

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.memory_model import MEMORY_STATUS_PENDING
from app.tasks.memory import _extract, _requeue_pending_memories


def test_duplicate_delivery_does_not_run_extraction():
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=0)),
        commit=AsyncMock(),
    )
    with patch("app.tasks.memory.run_extraction") as extract:
        asyncio.run(_extract(session, uuid.uuid4()))
    extract.assert_not_called()
    session.commit.assert_awaited_once()


def test_manual_remember_returns_pending_when_broker_is_down():
    from app.services.memory_service import MemoryService

    service = MemoryService(session=None)
    memory = SimpleNamespace(id=uuid.uuid4(), status=MEMORY_STATUS_PENDING)
    service.repo.create = AsyncMock(return_value=memory)
    with patch("app.tasks.memory.extract_memory_task.delay", side_effect=ConnectionError("broker down")):
        result = asyncio.run(service.remember(uuid.uuid4(), "我在研究 WiFi CSI"))
    assert result is memory
    assert result.status == MEMORY_STATUS_PENDING


def test_reconciler_republishes_old_pending_source():
    memory_id = uuid.uuid4()
    result = SimpleNamespace(scalars=lambda: [memory_id])
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_):
            return False

    engine = SimpleNamespace(dispose=AsyncMock())
    with (
        patch("app.tasks.memory.create_task_engine", return_value=engine),
        patch("app.tasks.memory.async_sessionmaker", return_value=lambda: SessionContext()),
        patch("app.tasks.memory.extract_memory_task.apply_async") as publish,
    ):
        count = asyncio.run(_requeue_pending_memories())
    assert count == 1
    publish.assert_called_once_with(args=(str(memory_id),), queue="memory")
    engine.dispose.assert_awaited_once()


def test_conflict_requeues_only_while_retries_remain():
    # The retry path must leave the source claimable, while a final conflict
    # must become visible as failed instead of looping forever.
    from app.core.memory.governance.service import MemoryRevisionConflict

    async def run_case(allow_retry):
        memory = SimpleNamespace(
            user_id=uuid.uuid4(), raw_text="x", source="auto",
            id=uuid.uuid4(), source_message_id=None, status=MEMORY_STATUS_PENDING,
            error_msg=None,
        )
        session = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(rowcount=1)),
            commit=AsyncMock(),
        )
        repo = SimpleNamespace(get_by_id=AsyncMock(return_value=memory), save=AsyncMock())
        with (
            patch("app.tasks.memory.MemoryRepository", return_value=repo),
            patch("app.tasks.memory.get_client_for_type", new_callable=AsyncMock),
            patch("app.tasks.memory.run_extraction", new_callable=AsyncMock,
                  side_effect=MemoryRevisionConflict("revision changed")),
        ):
            try:
                await _extract(session, memory.id, allow_conflict_retry=allow_retry)
            except MemoryRevisionConflict:
                if not allow_retry:
                    raise AssertionError("final conflict must not be retried")
            else:
                if allow_retry:
                    raise AssertionError("retryable conflict must be raised")
        return memory.status

    assert asyncio.run(run_case(True)) == "pending"
    assert asyncio.run(run_case(False)) == "failed"


def test_trace_does_not_persist_private_previews_by_default():
    from app.core.agent.tracing.models import SpanRecord
    from app.core.agent.tracing.tracer import _SpanHandle

    record = SpanRecord(
        trace_id=uuid.uuid4(), span_type="tool_call", name="knowledge_search",
        started_at=datetime.now(timezone.utc),
    )
    span = _SpanHandle(record)
    with patch("app.core.agent.tracing.tracer.settings.trace_capture_content", False):
        span.set_payload("output_preview", "private document")
        span.set_payload("output_chars", 16)
        span.set_attribute("zhiyi.tool.query", "private question")
        span.mark_error("token=secret")
    assert record.payload == {"output_chars": 16}
    assert "zhiyi.tool.query" not in record.attributes
    assert record.error_message == "error"


def test_production_rejects_default_signing_and_encryption_keys():
    from app.main import lifespan

    async def start():
        async with lifespan(None):
            pass

    with (
        patch("app.main.settings.app_env", "production"),
        patch("app.main.settings.jwt_secret", "change-me-in-production"),
        patch("app.main.settings.fernet_key", "change-me-fernet-key"),
    ):
        try:
            asyncio.run(start())
        except RuntimeError as exc:
            assert "JWT_SECRET" in str(exc)
        else:
            raise AssertionError("production must not start with example secrets")
