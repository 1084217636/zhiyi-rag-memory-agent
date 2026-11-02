"""Run a real Elasticsearch/IK retrieval smoke test on 100 frozen source files.

No model or fake embeddings are involved. This measures only lexical retrieval;
it must not be presented as hybrid RAG, rerank, answer accuracy or hallucination.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

from app.config import settings
from app.core.rag.chunker import ChunkConfig, chunk_parent_child
from app.core.rag.es_index import CHUNK_TYPE_CHILD, CHUNK_TYPE_PARENT, ensure_index
from app.core.rag.es_store import build_chunk_doc, bulk_index, delete_by_source
from eval.clients import close_clients, retrieve_bm25
from eval.metrics import recall_at_k

ROOT = Path(__file__).parent / "fixtures"
USER_ID = "eee00000-0000-0000-0000-0000000000f0"


async def main() -> None:
    files = sorted((ROOT / "corpus_100").glob("*.md"))
    gold = json.loads((ROOT / "gold" / "retrieval_100docs.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "corpus_100_manifest.json").read_text(encoding="utf-8"))
    assert len(files) == len(gold) == len(manifest) == 100
    assert len({f.stem for f in files}) == 100
    assert {f.stem for f in files} == {q["relevant_doc_ids"][0] for q in gold}
    config = ChunkConfig(parent_tokens=settings.rag_parent_chunk_tokens,
                         child_tokens=settings.rag_child_chunk_tokens,
                         overlap_tokens=settings.rag_child_overlap_tokens)
    await ensure_index()
    rows = []
    for path in files:
        # Only test-owned source IDs are touched. We do not reset the index.
        await delete_by_source(USER_ID, path.stem)
        for parent in chunk_parent_child(path.read_text(encoding="utf-8"), config):
            parent_row = build_chunk_doc(user_id=USER_ID, source_type="document",
                source_id=path.stem, doc_name=path.name, chunk_type=CHUNK_TYPE_PARENT,
                content=parent.content, vector=None)
            rows.append(parent_row)
            for child in parent.children:
                rows.append(build_chunk_doc(user_id=USER_ID, source_type="document",
                    source_id=path.stem, doc_name=path.name, chunk_type=CHUNK_TYPE_CHILD,
                    content=child, vector=None, parent_id=parent_row["_id"]))
    indexed = await bulk_index(rows)
    results, timings = [], []
    for case in gold:
        started = time.perf_counter()
        hits = await retrieve_bm25(USER_ID, case["question"], recall=20)
        timings.append((time.perf_counter() - started) * 1000)
        results.append({"source": case["relevant_doc_ids"][0], "question": case["question"],
                        "top5": hits[:5], "rank": (hits.index(case["relevant_doc_ids"][0]) + 1
                                                if case["relevant_doc_ids"][0] in hits else None)})
    report = {
        "test_type": "real_elasticsearch_ik_bm25_only",
        "corpus_files": len(files), "independent_sources": len(manifest),
        "questions": len(gold), "indexed_parent_and_child_chunks": indexed,
        "hit_at_1": sum(r["rank"] == 1 for r in results) / len(results),
        "recall_at_5": statistics.mean(recall_at_k(r["top5"], [r["source"]], 5) for r in results),
        "mrr_at_20": statistics.mean(1 / r["rank"] if r["rank"] else 0 for r in results),
        "p95_query_ms": sorted(timings)[94],
        "limitations": "Title-bearing questions, one relevant source each; no embedding, rerank, generation, independent human answer labels or production load.",
        "cases": results,
    }
    output = Path(__file__).parent / "results" / "bm25-100docs.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))
    print(f"details: {output}")
    await close_clients()


if __name__ == "__main__":
    asyncio.run(main())
