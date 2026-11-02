"""Distributed admission for cost-sensitive Agent runs.

One Redis Lua call checks the per-user minute limit and active-run leases.
The run timeout bounds a lease; finalization removes only its own token.
"""

import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.config import settings
from app.core.logging import get_logger
from app.db.redis import get_redis

logger = get_logger(__name__)

_ACQUIRE = """
local now = tonumber(ARGV[1])
local max_active = tonumber(ARGV[2])
local max_minute = tonumber(ARGV[3])
local lease_seconds = tonumber(ARGV[4])
local token = ARGV[5]
local max_global = tonumber(ARGV[6])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if redis.call('ZCARD', KEYS[2]) >= max_active then return 2 end
redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', now)
if redis.call('ZCARD', KEYS[3]) >= max_global then return 3 end
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], 60) end
if count > max_minute then return 1 end
redis.call('ZADD', KEYS[2], now + lease_seconds, token)
redis.call('EXPIRE', KEYS[2], lease_seconds)
redis.call('ZADD', KEYS[3], now + lease_seconds, token)
redis.call('EXPIRE', KEYS[3], lease_seconds)
return 0
"""


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str = ""


@asynccontextmanager
async def agent_run_slot(user_id: uuid.UUID):
    """Fail closed if the shared limiter is unavailable; never bill an unbounded run."""
    now = int(time.time())
    token = uuid.uuid4().hex
    minute_key = f"agent:minute:{user_id}:{now // 60}"
    active_key = f"agent:active:{user_id}"
    global_key = "agent:active:global"
    lease_seconds = max(1, int(settings.agent_run_timeout_seconds) + 5)
    redis = None
    acquired = False
    try:
        redis = get_redis()
        code = await redis.eval(
            _ACQUIRE, 3, minute_key, active_key, global_key, now,
            max(1, settings.agent_max_concurrent_runs_per_user),
            max(1, settings.agent_requests_per_minute), lease_seconds, token,
            max(1, settings.agent_max_concurrent_runs_global),
        )
        acquired = code == 0
    except Exception:
        logger.exception("Agent 请求准入检查失败")
        code = -1
    reason = {0: "", 1: "rate_limited", 2: "too_many_concurrent_runs", 3: "service_busy"}.get(
        code, "limiter_unavailable"
    )
    try:
        yield Admission(acquired, reason)
    finally:
        if acquired and redis is not None:
            try:
                pipe = redis.pipeline()
                pipe.zrem(active_key, token)
                pipe.zrem(global_key, token)
                await pipe.execute()
            except Exception:
                logger.exception("Agent 请求租约释放失败，等待 TTL 回收")
