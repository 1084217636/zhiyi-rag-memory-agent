"""Redis 异步客户端（显式连接池 + 健康检查）。"""
import asyncio
import weakref

from redis import asyncio as aioredis

from app.config import settings

_clients = weakref.WeakKeyDictionary()


def get_redis() -> aioredis.Redis:
    loop = asyncio.get_running_loop()
    entry = _clients.get(loop)
    if entry is None:
        pool = aioredis.ConnectionPool.from_url(
            settings.redis_url, decode_responses=True,
            max_connections=settings.redis_max_connections,health_check_interval=30)
        entry = (aioredis.Redis(connection_pool=pool),pool)
        _clients[loop] = entry
    return entry[0]


async def ping() -> bool:
    try:
        return await get_redis().ping()
    except Exception:
        return False


async def close() -> None:
    entry = _clients.pop(asyncio.get_running_loop(),None)
    if entry is not None:
        await entry[0].aclose()
        await entry[1].disconnect()
