"""Focused regression tests for ingestion boundaries and configurable retrieval."""

import io
import json
from pathlib import Path

import pytest

from app.core.rag.chunker import ChunkConfig, chunk_parent_child, count_tokens
from app.core.rag.fusion import FusionConfig, fuse_scores
from app.core.rag.parser import parse_document
from app.core.agent.verification import SAFE_ANSWER, verify_answer
from eval.tune_retrieval import _split, analyze
from eval.grounded_metrics import score_answers
from app.core.rag import query_embedding_cache


def test_long_unbroken_text_never_exceeds_chunk_limits():
    config = ChunkConfig(parent_tokens=128, child_tokens=48, overlap_tokens=8)
    parents = chunk_parent_child("甲" * 1200, config)
    assert len(parents) > 1
    assert all(count_tokens(p.content) <= 128 for p in parents)
    assert all(count_tokens(c) <= 48 for p in parents for c in p.children)


def test_headings_separate_parents_and_keep_context():
    parents = chunk_parent_child("# 第一章\n旧结论。\n# 第二章\n新结论。")
    assert len(parents) == 2
    assert "第一章" in parents[0].content and "第二章" not in parents[0].content
    assert "第二章" in parents[1].content and "新结论" in parents[1].content
    markdown_text = parse_document("md", b"# Chapter\n|Name|Score|\n|---|---|\n|Alice|95|")
    assert "# Chapter" in markdown_text and "|Alice|95|" in markdown_text


def test_csv_and_xlsx_rows_keep_column_and_location():
    csv_text = parse_document(".csv", "姓名,分数\n张三,95\n李四,88".encode())
    assert "工作表 CSV 第 2 行 | 姓名: 张三 | 分数: 95" in csv_text

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "成绩"
    sheet.append(["姓名", "分数"])
    sheet.append(["张三", 95])
    content = io.BytesIO()
    book.save(content)
    parsed = parse_document(".xlsx", content.getvalue())
    assert "工作表 成绩 第 2 行 | 姓名: 张三 | 分数: 95" in parsed


def test_docx_table_is_not_dropped():
    from docx import Document

    doc = Document()
    doc.add_heading("实验记录", 1)
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "样本"
    table.rows[0].cells[1].text = "42"
    content = io.BytesIO()
    doc.save(content)
    parsed = parse_document("docx", content.getvalue())
    assert "# 实验记录" in parsed
    assert "样本 | 42" in parsed


def test_fusion_has_explicit_weights_and_rank_based_alternative():
    vector = {"a": 0.9, "b": 0.8}
    lexical = {"b": 12.0, "c": 11.0}
    assert fuse_scores(vector, lexical, FusionConfig(vector_weight=1))["a"] > 0
    lexical_only = fuse_scores(vector, lexical, FusionConfig(vector_weight=0))
    assert lexical_only["b"] > lexical_only["c"]
    rrf = fuse_scores(vector, lexical, FusionConfig(method="rrf"))
    assert rrf["b"] > rrf["a"] and rrf["b"] > rrf["c"]
    with pytest.raises(ValueError):
        FusionConfig(vector_weight=1.1)


@pytest.mark.asyncio
async def test_query_embedding_cache_is_scoped_and_falls_back_on_redis_error(monkeypatch):
    class Redis:
        def __init__(self): self.values = {}
        async def get(self, key): return self.values.get(key)
        async def set(self, key, value, ex): self.values[key] = value

    class Embedder:
        model_name = "embedding-v1"
        calls = 0
        async def embed_one(self, query):
            self.calls += 1
            return [0.1, 0.2]

    redis = Redis()
    monkeypatch.setattr(query_embedding_cache, "get_redis", lambda: redis)
    monkeypatch.setattr(query_embedding_cache.settings, "rag_query_embedding_cache_ttl_seconds", 60)
    model = Embedder()
    assert await query_embedding_cache.embed_query(model, "user-a", "WiFi   CSI") == [0.1, 0.2]
    assert await query_embedding_cache.embed_query(model, "user-a", "WiFi CSI") == [0.1, 0.2]
    assert model.calls == 1
    assert await query_embedding_cache.embed_query(model, "user-b", "wifi csi") == [0.1, 0.2]
    assert model.calls == 2

    class BrokenRedis:
        async def get(self, key): raise RuntimeError("offline")
        async def set(self, key, value, ex): raise RuntimeError("offline")
    monkeypatch.setattr(query_embedding_cache, "get_redis", lambda: BrokenRedis())
    assert await query_embedding_cache.embed_query(model, "user-a", "another query") == [0.1, 0.2]
    assert model.calls == 3


@pytest.mark.asyncio
async def test_unknown_citation_fails_closed_without_model_call():
    result = await verify_answer(
        None, "研究结论是什么？", "结论是 95%。[K999]",
        [{"evidence_id": "K1", "content": "结论是 80%。"}],
        required=True, repair_limit=0,
    )
    assert result.status == "unknown_citation"
    assert result.text == SAFE_ANSWER


def test_weight_experiment_keeps_small_sample_inconclusive():
    candidates = {
        "chunks": {"a": "doc-a", "b": "doc-b"},
        "vector_scores": {"a": 0.9, "b": 0.8},
        "bm25_scores": {"b": 10, "a": 5},
    }
    payload = {"corpus_fingerprint": "fixture", "embedding_model": "fake",
               "rows": [{"question": f"q{i}", "relevant_doc_ids": ["doc-a"],
                         "candidates": candidates} for i in range(10)]}
    result = analyze(payload)
    assert result["questions"] == 10
    assert result["decision"] == "inconclusive_keep_default"
    assert result["dev"]["vector"]["mrr"] > result["dev"]["bm25"]["mrr"]


def test_shared_gold_documents_never_cross_tuning_split():
    rows = [
        {"question": "a", "relevant_doc_ids": ["doc-a"]},
        {"question": "a+b", "relevant_doc_ids": ["doc-a", "doc-b"]},
        {"question": "b", "relevant_doc_ids": ["doc-b"]},
        {"question": "c", "relevant_doc_ids": ["doc-c"]},
    ]
    dev, holdout = _split(rows)
    dev_docs = {doc for row in dev for doc in row["relevant_doc_ids"]}
    holdout_docs = {doc for row in holdout for doc in row["relevant_doc_ids"]}
    assert dev_docs.isdisjoint(holdout_docs)


def test_expanded_eval_sets_have_100_rows_and_preserve_query_groups():
    root = Path(__file__).parents[1] / "eval" / "fixtures" / "gold"
    rag = json.loads((root / "retrieval_100.json").read_text(encoding="utf-8"))
    memory = json.loads((root / "memory_retrieval_100.json").read_text(encoding="utf-8"))
    assert len(rag) == len(memory) == 100
    assert len({row["question"] for row in rag}) == 100
    assert len({row["question"] for row in memory}) == 100
    assert len({row["query_group"] for row in rag}) == 20
    assert len({row["query_group"] for row in memory}) == 19
    assert all(row.get("relevant_doc_ids") for row in rag)
    assert all(row.get("relevant_entities") for row in memory)


def test_grounded_metrics_separate_refusal_and_claim_support():
    result = score_answers([
        {"answerable": True, "refusal": False, "cited_evidence_ids": ["K1"],
         "gold_evidence_ids": ["K1"], "claim_support": [True, False], "latency_ms": 100},
        {"answerable": False, "refusal": True, "cited_evidence_ids": [],
         "gold_evidence_ids": [], "claim_support": [], "latency_ms": 200},
    ])
    assert result["correct_refusal_rate"] == 1
    assert result["false_refusal_rate"] == 0
    assert result["supported_claim_rate"] == 0.5
    assert result["citation_precision"] == 1
