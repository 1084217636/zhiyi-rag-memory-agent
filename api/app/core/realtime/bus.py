"""群聊实时事件总线 —— 基于 Redis 发布订阅（Pub/Sub）。

多人实时群聊里，任意成员发言或 AI 角色逐字回答，都通过 publish 把事件发到该群聊
会话的 Redis 频道；每个在场成员开一条 SSE 长连接订阅同一频道，从而实现「谁发消息
全员秒级可见」。复用现有 Redis（Celery broker），跨进程/多 worker 天然广播。

事件结构：{"event": "human_message" | "token" | ..., "data": {...}}

另提供「发言锁」：多个真人同时发言时，避免 AI 调度被重复触发（同一会话同一时刻
只允许一个 AI 回合在生成）。
"""
import json
from collections.abc import AsyncGenerator

from app.core.logging import get_logger
from app.db.redis import get_redis

logger = get_logger(__name__)

# 频道与锁的 key 前缀
_CHANNEL_PREFIX = "groupchat:"
_LOCK_PREFIX = "groupchat:lock:"
# AI 回合锁的最大持有时间（秒），防异常导致死锁
_LOCK_TTL = 120
# 订阅空闲心跳间隔（秒），保活长连接，避免反向代理空闲超时断开
_PING_INTERVAL = 25


def channel_for(conv_id: str) -> str:
    return f"{_CHANNEL_PREFIX}{conv_id}"


def _lock_key(conv_id: str) -> str:
    return f"{_LOCK_PREFIX}{conv_id}"


async def publish(conv_id: str, event: str, data: dict) -> None:
    """向群聊频道广播一个事件。失败只记日志，不阻断主流程。"""
    try:
        payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)
        await get_redis().publish(channel_for(conv_id), payload)
    except Exception as e:
        logger.warning("群聊事件广播失败: conv=%s event=%s err=%s", conv_id, event, e)


async def subscribe(conv_id: str) -> AsyncGenerator[dict, None]:
    """订阅群聊频道，持续产出 {"event":..., "data":...} 事件。

    空闲时（无新消息）周期性产出 {"event": "_ping"} 心跳，供上层发 SSE 注释保活，
    避免 nginx/反向代理对长时间无数据的连接做空闲超时断开。
    调用方负责在客户端断开时让本协程结束，finally 会清理订阅。
    """
    redis = get_redis()
    pubsub = redis.pubsub()
    channel = channel_for(conv_id)
    await pubsub.subscribe(channel)
    try:
        while True:
            try:
                raw = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=_PING_INTERVAL
                )
            except Exception as e:
                logger.warning("群聊订阅读取失败: conv=%s err=%s", conv_id, e)
                break
            if raw is None:
                # 超时未收到消息：吐心跳保活
                yield {"event": "_ping"}
                continue
            if raw.get("type") != "message":
                continue
            data = raw.get("data")
            if not data:
                continue
            try:
                yield json.loads(data)
            except (ValueError, TypeError) as e:
                logger.warning("群聊事件解析失败（跳过）: %s", e)
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception as e:
            logger.warning("群聊订阅清理失败: conv=%s err=%s", conv_id, e)


async def open_channel(conv_id: str):
    """打开一个订阅（返回已 subscribe 的 pubsub 对象）。

    与 subscribe() 不同：本函数把「建立订阅」与「读取消息」拆开，调用方可以先 open_channel
    确保订阅就绪，再去触发会产生事件的动作，从而消除「事件早于订阅而漏收」的竞态。
    读取用 iter_channel()，结束务必 close_channel()。
    """
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(channel_for(conv_id))
    return pubsub


async def iter_channel(pubsub, conv_id: str) -> AsyncGenerator[dict, None]:
    """从已打开的 pubsub 持续读取事件；空闲吐 {"event":"_ping"} 心跳保活。"""
    while True:
        try:
            raw = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_PING_INTERVAL
            )
        except Exception as e:
            logger.warning("订阅读取失败: conv=%s err=%s", conv_id, e)
            break
        if raw is None:
            yield {"event": "_ping"}
            continue
        if raw.get("type") != "message":
            continue
        data = raw.get("data")
        if not data:
            continue
        try:
            yield json.loads(data)
        except (ValueError, TypeError) as e:
            logger.warning("事件解析失败（跳过）: %s", e)


async def close_channel(pubsub, conv_id: str) -> None:
    """关闭订阅，释放资源。"""
    try:
        await pubsub.unsubscribe(channel_for(conv_id))
        await pubsub.aclose()
    except Exception as e:
        logger.warning("订阅清理失败: conv=%s err=%s", conv_id, e)


# ── 单聊流式缓冲：支持「断线重连续传」——把生成中累积的内容写 Redis，重连时补推 ──
_STREAM_BUF_PREFIX = "chatstream:buf:"
# 缓冲存活时间（秒）：覆盖一次最慢生成（含工具/多模态），过期自动清理防泄漏
_STREAM_BUF_TTL = 600


def _stream_buf_key(conv_id: str) -> str:
    return f"{_STREAM_BUF_PREFIX}{conv_id}"


async def set_stream_buffer(conv_id: str, data: dict) -> None:
    """写/刷新某会话「生成中」的累积内容（content / n / citations / tool_calls / status）。"""
    try:
        await get_redis().set(
            _stream_buf_key(conv_id),
            json.dumps(data, ensure_ascii=False),
            ex=_STREAM_BUF_TTL,
        )
    except Exception as e:
        logger.warning("写流式缓冲失败: conv=%s err=%s", conv_id, e)


async def get_stream_buffer(conv_id: str) -> dict | None:
    """读某会话的流式缓冲；无则 None。"""
    try:
        raw = await get_redis().get(_stream_buf_key(conv_id))
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.warning("读流式缓冲失败: conv=%s err=%s", conv_id, e)
        return None


async def clear_stream_buffer(conv_id: str) -> None:
    try:
        await get_redis().delete(_stream_buf_key(conv_id))
    except Exception as e:
        logger.warning("清流式缓冲失败: conv=%s err=%s", conv_id, e)


async def acquire_turn_lock(conv_id: str) -> bool:
    """尝试拿下某会话的 AI 回合锁（SET NX EX）。拿到返回 True。"""
    try:
        ok = await get_redis().set(_lock_key(conv_id), "1", nx=True, ex=_LOCK_TTL)
        return bool(ok)
    except Exception as e:
        logger.warning("获取群聊回合锁失败: conv=%s err=%s", conv_id, e)
        # 拿锁失败时保守放行（宁可偶发重复也不卡死对话）
        return True


async def release_turn_lock(conv_id: str) -> None:
    try:
        await get_redis().delete(_lock_key(conv_id))
    except Exception as e:
        logger.warning("释放群聊回合锁失败: conv=%s err=%s", conv_id, e)


# ── 在线状态（presence）：用有序集合存「user_id -> 最近心跳时间戳」，过期判离线 ──
_ONLINE_PREFIX = "groupchat:online:"
# 超过该秒数没有心跳即视为离线（心跳间隔 _PING_INTERVAL=25s，留足余量）
_ONLINE_TTL = 60


def _online_key(conv_id: str) -> str:
    return f"{_ONLINE_PREFIX}{conv_id}"


async def mark_online(conv_id: str, user_id: str) -> None:
    """标记某用户在该群在线（写入/刷新心跳时间戳）。"""
    import time

    try:
        await get_redis().zadd(_online_key(conv_id), {str(user_id): time.time()})
    except Exception as e:
        logger.warning("标记在线失败: conv=%s user=%s err=%s", conv_id, user_id, e)


async def mark_offline(conv_id: str, user_id: str) -> None:
    """移除某用户的在线标记。"""
    try:
        await get_redis().zrem(_online_key(conv_id), str(user_id))
    except Exception as e:
        logger.warning("标记离线失败: conv=%s user=%s err=%s", conv_id, user_id, e)


async def list_online(conv_id: str) -> set[str]:
    """返回该群当前在线的 user_id 集合（清理超时项后取剩余）。"""
    import time

    try:
        r = get_redis()
        cutoff = time.time() - _ONLINE_TTL
        await r.zremrangebyscore(_online_key(conv_id), 0, cutoff)
        members = await r.zrange(_online_key(conv_id), 0, -1)
        return {str(m) for m in members}
    except Exception as e:
        logger.warning("获取在线列表失败: conv=%s err=%s", conv_id, e)
        return set()
