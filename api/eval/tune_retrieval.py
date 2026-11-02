"""Frozen-candidate retrieval experiment; never rewrites application settings.

Collect vector/BM25 candidates once against an isolated evaluation user, then
compare fusion policies on the same snapshot. A held-out slice is reported once;
small or saturated sets are explicitly inconclusive.
"""

import argparse
import asyncio
import hashlib
import json
import random
from pathlib import Path

from app.core.rag.fusion import FusionConfig
from app.core.llm.client import close_llm_client
from eval import clients, metrics
from eval.eval_config import EVAL_USER_ID, embed_client

HERE = Path(__file__).parent
DEFAULT_GOLD = HERE / "fixtures/gold/retrieval_100.json"
DEFAULT_SNAPSHOT = HERE / "results/retrieval-candidates.json"
POLICIES = {
    "bm25": FusionConfig(vector_weight=0),
    "weighted_025": FusionConfig(vector_weight=0.25),
    "weighted_050": FusionConfig(vector_weight=0.5),
    "weighted_060": FusionConfig(vector_weight=0.6),
    "weighted_075": FusionConfig(vector_weight=0.75),
    "vector": FusionConfig(vector_weight=1),
    "rrf_60": FusionConfig(method="rrf", rrf_k=60),
}


def corpus_fingerprint(corpus: Path, gold: Path) -> str:
    digest = hashlib.sha256()
    for path in [*sorted(corpus.glob("*.md")), *sorted(corpus.glob("*.txt")), gold]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


async def collect(gold: Path, out: Path, recall: int = 20) -> dict:
    questions = json.loads(gold.read_text(encoding="utf-8"))
    model = embed_client()
    rows = []
    try:
        for number, item in enumerate(questions, 1):
            print(f"[retrieval] {number}/{len(questions)}")
            candidates = await clients.candidate_snapshot(
                model, str(EVAL_USER_ID), item["question"], recall
            )
            rows.append({**item, "candidates": candidates})
    finally:
        await close_llm_client()
        await clients.close_clients()
    payload = {
        "corpus_fingerprint": corpus_fingerprint(HERE / "fixtures/corpus", gold),
        "embedding_model": model.model_name,
        "recall": recall,
        "rows": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _source_groups(rows: list[dict]) -> dict[str, list[dict]]:
    """Keep questions sharing any gold document in the same group."""
    parent: dict[str, str] = {}

    def root(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = root(parent[value])
        return parent[value]

    for row in rows:
        docs = row.get("relevant_doc_ids") or ["question:" + row["question"]]
        for doc in docs[1:]:
            parent[root(doc)] = root(docs[0])
    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = root((row.get("relevant_doc_ids") or ["question:" + row["question"]])[0])
        groups.setdefault(key, []).append(row)
    return groups


def _split(rows: list[dict], seed: int = 42) -> tuple[list[dict], list[dict]]:
    # Queries about one source are correlated. Keep connected gold sources
    # wholly in one split instead of leaking them across dev/holdout.
    groups = _source_groups(rows)
    keys = sorted(groups)
    if len(keys) < 2:
        return rows[:], []
    random.Random(seed).shuffle(keys)
    n_holdout = max(1, round(len(keys) * 0.3))
    holdout_keys = set(keys[:n_holdout])
    return ([row for key in keys if key not in holdout_keys for row in groups[key]],
            [row for key in keys if key in holdout_keys for row in groups[key]])


def _per_query(row: dict, config: FusionConfig, k: int) -> dict[str, float]:
    # Serving returns the top-k chunks, which may include the same document
    # more than once. Deduplicate only inside that actual top-k window.
    ranked = clients.rank_snapshot(row["candidates"], config, chunk_limit=k)
    gold = row["relevant_doc_ids"]
    return {
        "recall": metrics.recall_at_k(ranked, gold, k),
        "mrr": metrics.mrr(ranked, gold),
        "ndcg": metrics.ndcg_at_k(ranked, gold, k),
    }


def _aggregate(rows: list[dict], config: FusionConfig, k: int) -> dict[str, float]:
    scores = [_per_query(row, config, k) for row in rows]
    return {key: round(metrics.avg([s[key] for s in scores]), 4)
            for key in ("recall", "mrr", "ndcg")}


def _paired_interval(rows: list[dict], candidate: FusionConfig,
                     baseline: FusionConfig, k: int, seed: int = 42) -> list[float]:
    if not rows:
        return [0.0, 0.0]
    by_source = _source_groups(rows)
    # Resample source groups, not individual questions about the same source.
    differences = [sum(_per_query(row, candidate, k)["ndcg"]
                       - _per_query(row, baseline, k)["ndcg"] for row in group) / len(group)
                   for group in by_source.values()]
    rng = random.Random(seed)
    samples = sorted(sum(rng.choices(differences, k=len(differences))) / len(differences)
                     for _ in range(1000))
    return [round(samples[25], 4), round(samples[974], 4)]


def analyze(payload: dict, k: int = 5) -> dict:
    rows = payload["rows"]
    if len(rows) < 2:
        raise ValueError("at least two labeled questions are needed")
    dev, holdout = _split(rows)
    dev_results = {name: _aggregate(dev, config, k) for name, config in POLICIES.items()}
    best = max(POLICIES, key=lambda name: (
        dev_results[name]["ndcg"], dev_results[name]["mrr"],
        name == "weighted_060",
    ))
    holdout_results = ({name: _aggregate(holdout, config, k)
                        for name, config in POLICIES.items()} if holdout else {})
    interval = (_paired_interval(holdout, POLICIES[best], POLICIES["weighted_060"], k)
                if holdout else None)
    distinct_docs = len({doc for row in rows for doc in row["relevant_doc_ids"]})
    enough = len(rows) >= 100 and distinct_docs >= 20
    return {
        "corpus_fingerprint": payload.get("corpus_fingerprint"),
        "embedding_model": payload.get("embedding_model"),
        "questions": len(rows),
        "distinct_relevant_docs": distinct_docs,
        "dev_questions": len(dev),
        "holdout_questions": len(holdout),
        "dev_source_groups": len(_source_groups(dev)),
        "holdout_source_groups": len(_source_groups(holdout)),
        "k": k,
        "dev": dev_results,
        "holdout": holdout_results,
        "selected_on_dev": best,
        "holdout_ndcg_delta_ci95_vs_default": interval,
        "decision": "candidate_for_review" if enough and interval and interval[0] > 0
                    else "inconclusive_keep_default",
        "note": "The holdout is descriptive; no configuration changes are made automatically.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    if args.collect:
        payload = asyncio.run(collect(args.gold, args.snapshot))
    else:
        payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
    result = analyze(payload, k=args.k)
    out = args.snapshot.with_name(args.snapshot.stem + "-analysis.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
