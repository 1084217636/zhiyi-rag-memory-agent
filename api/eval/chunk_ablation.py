"""Compare chunk sizes on the isolated fixture user, then restore the default index.

The result is descriptive for this tiny corpus. It must not auto-change settings.
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.config import settings
from app.core.llm.client import close_llm_client
from eval import clients
from eval.eval_config import embed_client
from eval.pipeline.setup import ingest_corpus
from eval.tasks.retrieval import eval_rag
from eval.tune_retrieval import corpus_fingerprint

HERE = Path(__file__).parent


async def run() -> dict:
    model = embed_client()
    original = (settings.rag_parent_chunk_tokens,
                settings.rag_child_chunk_tokens, settings.rag_child_overlap_tokens)
    variants = [(1024, 128, 16), (1024, 256, 24), (1024, 384, 24)]
    results = []
    try:
        for parent, child, overlap in variants:
            settings.rag_parent_chunk_tokens = parent
            settings.rag_child_chunk_tokens = child
            settings.rag_child_overlap_tokens = overlap
            await ingest_corpus(model)
            metrics, _ = await eval_rag(model, None)
            results.append({"parent_tokens": parent, "child_tokens": child,
                            "overlap_tokens": overlap, "metrics": metrics})
        return {
            "corpus_fingerprint": corpus_fingerprint(
                HERE / "fixtures/corpus", HERE / "fixtures/gold/retrieval.json"),
            "embedding_model": model.model_name,
            "question_count": len(json.loads(
                (HERE / "fixtures/gold/retrieval.json").read_text(encoding="utf-8"))),
            "variants": results,
            "interpretation": "small fixture; report differences but do not select a global optimum",
        }
    finally:
        # Restore the original configuration and the original fixture chunks.
        (settings.rag_parent_chunk_tokens,
         settings.rag_child_chunk_tokens,
         settings.rag_child_overlap_tokens) = original
        try:
            await ingest_corpus(model)
        finally:
            await close_llm_client()
            await clients.close_clients()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "results/chunk-ablation.json")
    args = parser.parse_args()
    result = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
