"""Elasticsearch 异步客户端（单例 + 连接池/超时/重试参数显式化）。"""
import asyncio
import weakref

from elasticsearch import AsyncElasticsearch

from app.config import settings

_clients = weakref.WeakKeyDictionary()


def get_es() -> AsyncElasticsearch:
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        auth = None
        if settings.es_username:
            auth = (settings.es_username, settings.es_password)
        client = AsyncElasticsearch(
            hosts=[settings.es_host],
            basic_auth=auth,
            max_retries=settings.es_max_retries,
            retry_on_timeout=True,
            request_timeout=settings.es_request_timeout,
            # 连接池：单节点最大连接数
            connections_per_node=settings.es_max_connections,
        )
        _clients[loop] = client
    return client


async def ping() -> bool:
    try:
        return await get_es().ping()
    except Exception:
        return False


async def close() -> None:
    client = _clients.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.close()
