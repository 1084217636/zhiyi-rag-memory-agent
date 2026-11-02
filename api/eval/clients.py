"""存储客户端访问 + ES 检索变体（文档粒度，返回排序后的 source_id）。

检索变体逻辑参照 app/core/rag/search.py，便于做「纯向量/纯BM25/混合/+rerank」四配置对比；
embed_client / rerank_client 由调用方注入（来自 eval_config，不读 app 用户配置）。
"""
from app.core.rag.es_index import CHUNK_TYPE_CHILD, CHUNKS_INDEX
from app.core.rag.fusion import FusionConfig, fuse_scores
from app.db.elastic import close as _es_close
from app.db.elastic import get_es
from app.db.neo4j import close as _neo_close


def _base_filter(uid: str) -> list[dict]:
    return [{"term": {"user_id": uid}}, {"term": {"chunk_type": CHUNK_TYPE_CHILD}}]


def _dedup_sources(hits: list[dict]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for h in hits:
        sid = h["_source"].get("source_id")
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


async def retrieve_vector(embed_client, uid: str, query: str, recall: int = 20) -> list[str]:
    es = get_es()
    qv = await embed_client.embed_one(query)
    resp = await es.search(index=CHUNKS_INDEX, body={
        "size": recall,
        "knn": {"field": "vector", "query_vector": qv, "k": recall,
                "num_candidates": recall * 5, "filter": {"bool": {"filter": _base_filter(uid)}}},
    })
    return _dedup_sources(resp["hits"]["hits"])


async def retrieve_bm25(uid: str, query: str, recall: int = 20) -> list[str]:
    es = get_es()
    resp = await es.search(index=CHUNKS_INDEX, body={
        "size": recall,
        "query": {"bool": {"must": [{"match": {"content": query}}], "filter": _base_filter(uid)}},
    })
    return _dedup_sources(resp["hits"]["hits"])


async def retrieve_hybrid(embed_client, uid: str, query: str, recall: int = 20,
                          wv: float = 0.6, wb: float = 0.4,
                          method: str = "weighted") -> list[str]:
    es = get_es()
    qv = await embed_client.embed_one(query)
    knn = await es.search(index=CHUNKS_INDEX, body={
        "size": recall,
        "knn": {"field": "vector", "query_vector": qv, "k": recall, "num_candidates": recall * 5,
                "filter": {"bool": {"filter": _base_filter(uid)}}}})
    bm = await es.search(index=CHUNKS_INDEX, body={
        "size": recall, "query": {"bool": {"must": [{"match": {"content": query}}],
                                           "filter": _base_filter(uid)}}})
    chunk_src: dict[str, str] = {}
    vs: dict[str, float] = {}
    bs: dict[str, float] = {}
    for h in knn["hits"]["hits"]:
        vs[h["_id"]] = h["_score"]
        chunk_src[h["_id"]] = h["_source"].get("source_id")
    for h in bm["hits"]["hits"]:
        bs[h["_id"]] = h["_score"]
        chunk_src[h["_id"]] = h["_source"].get("source_id")
    if abs(wv + wb - 1) > 1e-9:
        raise ValueError("fusion weights must sum to one")
    fused = fuse_scores(vs, bs, FusionConfig(method=method, vector_weight=wv))
    seen: set[str] = set()
    out: list[str] = []
    for cid in sorted(fused, key=fused.get, reverse=True):
        sid = chunk_src.get(cid)
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


async def candidate_snapshot(embed_client, uid: str, query: str, recall: int = 20) -> dict:
    """Collect both routes once; offline sweeps must not re-query a changing index."""
    es = get_es()
    query_vector = await embed_client.embed_one(query)
    vector = await es.search(index=CHUNKS_INDEX, body={
        "size": recall,
        "knn": {"field": "vector", "query_vector": query_vector, "k": recall,
                "num_candidates": recall * 5, "filter": {"bool": {"filter": _base_filter(uid)}}},
    })
    lexical = await es.search(index=CHUNKS_INDEX, body={
        "size": recall,
        "query": {"bool": {"must": [{"match": {"content": query}}],
                        "filter": _base_filter(uid)}},
    })
    chunks = {}
    for response in (vector, lexical):
        for hit in response["hits"]["hits"]:
            chunks[hit["_id"]] = hit["_source"].get("source_id")
    return {
        "chunks": chunks,
        "vector_scores": {hit["_id"]: hit["_score"] for hit in vector["hits"]["hits"]},
        "bm25_scores": {hit["_id"]: hit["_score"] for hit in lexical["hits"]["hits"]},
    }


def rank_snapshot(snapshot: dict, config: FusionConfig,
                  chunk_limit: int | None = None) -> list[str]:
    fused = fuse_scores(snapshot["vector_scores"], snapshot["bm25_scores"], config)
    sources: list[str] = []
    seen: set[str] = set()
    ordered = sorted(fused, key=lambda cid: (-fused[cid], cid))
    for chunk_id in ordered[:chunk_limit]:
        source = snapshot["chunks"].get(chunk_id)
        if source and source not in seen:
            seen.add(source)
            sources.append(source)
    return sources


async def rerank_sources(rerank_client, uid: str, query: str,
                         source_ids: list[str], top_k: int) -> list[str]:
    """对候选 source 取代表 chunk 内容做 cross-encoder rerank，返回重排后的 source_id。"""
    if not source_ids:
        return []
    es = get_es()
    contents: list[str] = []
    for sid in source_ids:
        resp = await es.search(index=CHUNKS_INDEX, body={
            "size": 1,
            "query": {"bool": {"filter": [
                {"term": {"user_id": uid}}, {"term": {"source_id": sid}},
            ]}},
        })
        hits = resp["hits"]["hits"]
        contents.append(hits[0]["_source"].get("content", "") if hits else "")
    pairs = await rerank_client.rerank(query, contents, top_n=top_k)
    return [source_ids[idx] for idx, _ in pairs]


async def close_clients() -> None:
    for closer in (_es_close, _neo_close):
        try:
            await closer()
        except Exception:
            pass
