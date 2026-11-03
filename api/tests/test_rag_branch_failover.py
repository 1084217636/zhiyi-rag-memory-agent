"""A healthy retrieval branch remains usable when its peer is unavailable."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.rag import search


def _hit(chunk_id: str, score: float = 0.9) -> dict:
    return {
        "_id": chunk_id,
        "_score": score,
        "_source": {
            "content": "实验记录：样本 A 的结果为 42。",
            "source_id": "document-a",
            "source_type": "document",
            "doc_name": "实验记录",
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_branch", ["vector", "bm25"])
async def test_search_uses_healthy_branch(monkeypatch, failed_branch):
    class ES:
        async def search(self, *, index, body):
            branch = "vector" if "knn" in body else "bm25"
            if branch == failed_branch:
                raise ConnectionError(f"{branch} unavailable")
            return {"hits": {"hits": [_hit("chunk-a")]}}

    monkeypatch.setattr(search, "get_es", ES)
    monkeypatch.setattr(search, "get_client_for_type", AsyncMock(return_value=object()))
    monkeypatch.setattr(search, "get_optional_client_for_type", AsyncMock(return_value=None))
    monkeypatch.setattr(search, "embed_query", AsyncMock(return_value=[0.1, 0.2]))

    rows = await search.hybrid_search(None, uuid.uuid4(), "样本 A 的结果", top_k=5)

    assert len(rows) == 1
    assert rows[0]["source_id"] == "document-a"
    assert rows[0]["retrieval_signals"]["bm25"] == (0.0 if failed_branch == "bm25" else 0.9)


@pytest.mark.asyncio
async def test_both_branches_down_is_error_not_empty_evidence(monkeypatch):
    class ES:
        async def search(self, *, index, body):
            raise ConnectionError("storage unavailable")

    monkeypatch.setattr(search, "get_es", ES)
    monkeypatch.setattr(search, "get_client_for_type", AsyncMock(return_value=object()))
    monkeypatch.setattr(search, "embed_query", AsyncMock(return_value=[0.1, 0.2]))

    with pytest.raises(ConnectionError, match="storage unavailable"):
        await search.hybrid_search(None, uuid.uuid4(), "样本 A 的结果")
