"""Neo4j 异步驱动连接（单例 + 连接池参数显式化）。"""
import asyncio
import weakref

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.config import settings

_drivers = weakref.WeakKeyDictionary()


def get_driver() -> AsyncDriver:
    loop = asyncio.get_running_loop()
    driver = _drivers.get(loop)
    if driver is None:
        driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_pool_size=settings.neo4j_max_pool_size,
            connection_acquisition_timeout=settings.neo4j_connection_timeout,
        )
        _drivers[loop] = driver
    return driver


async def ping() -> bool:
    try:
        await get_driver().verify_connectivity()
        return True
    except Exception:
        return False


async def close() -> None:
    driver = _drivers.pop(asyncio.get_running_loop(), None)
    if driver is not None:
        await driver.close()
