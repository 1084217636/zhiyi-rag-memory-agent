"""Short lived, user-scoped cache for query embeddings (never caches documents)."""
import hashlib
import json
import re

from app.config import settings
from app.core.logging import get_logger
from app.db.redis import get_redis

logger = get_logger(__name__)


async def embed_query(client, user_id, query: str) -> list[float]:
    """Reuse a query vector briefly; Redis failures fall back to the provider.

    Cache keys include user, model and normalized query. Only vectors are stored,
    so document edits/deletions cannot make stale source text or citations leak.
    """
    normalized = re.sub(r"\s+", " ", query).strip()
    if not normalized:
        return await client.embed_one(query)

    model = "|".join((
        str(getattr(client, "base_url", "unknown-endpoint")),
        str(getattr(client, "model_name", "unknown-model")),
    ))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    model_digest = hashlib.sha256(model.encode("utf-8")).hexdigest()[:16]
    key = f"rag:qemb:{user_id}:{model_digest}:{digest}"
    redis = None
    try:
        redis = get_redis()
        cached = await redis.get(key)
        if cached:
            vector = json.loads(cached)
            if isinstance(vector, list) and vector and all(isinstance(x, (int, float)) for x in vector):
                return [float(x) for x in vector]
    except Exception as exc:
        logger.warning("查询向量缓存读取失败，回源模型: %s", type(exc).__name__)

    vector = await client.embed_one(query)
    ttl = max(0, int(settings.rag_query_embedding_cache_ttl_seconds))
    if redis is not None and ttl:
        try:
            await redis.set(key, json.dumps(vector, separators=(",", ":")), ex=ttl)
        except Exception as exc:
            logger.warning("查询向量缓存写入失败: %s", type(exc).__name__)
    return vector
