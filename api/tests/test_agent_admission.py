import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.agent.admission import agent_run_slot


def test_slot_is_released_when_chat_raises():
    pipe = SimpleNamespace(zrem=lambda *_: None, execute=AsyncMock())
    redis = SimpleNamespace(eval=AsyncMock(return_value=0), pipeline=lambda: pipe)

    async def run():
        async with agent_run_slot(uuid.uuid4()) as admission:
            assert admission.allowed
            raise ValueError("model crashed")

    with patch("app.core.agent.admission.get_redis", return_value=redis):
        try:
            asyncio.run(run())
        except ValueError:
            pass
        else:
            raise AssertionError("chat exception must propagate")
    assert pipe.execute.await_count == 1


def test_rate_limit_and_redis_outage_fail_closed():
    async def run(redis):
        with patch("app.core.agent.admission.get_redis", return_value=redis):
            async with agent_run_slot(uuid.uuid4()) as admission:
                return admission

    rate_redis = SimpleNamespace(eval=AsyncMock(return_value=1))
    limited = asyncio.run(run(rate_redis))
    assert not limited.allowed and limited.reason == "rate_limited"

    down_redis = SimpleNamespace(eval=AsyncMock(side_effect=ConnectionError("down")))
    unavailable = asyncio.run(run(down_redis))
    assert not unavailable.allowed and unavailable.reason == "limiter_unavailable"


def test_global_concurrency_limit_is_reported():
    redis = SimpleNamespace(eval=AsyncMock(return_value=3))

    async def run():
        with patch("app.core.agent.admission.get_redis", return_value=redis):
            async with agent_run_slot(uuid.uuid4()) as admission:
                return admission

    admission = asyncio.run(run())
    assert not admission.allowed and admission.reason == "service_busy"
