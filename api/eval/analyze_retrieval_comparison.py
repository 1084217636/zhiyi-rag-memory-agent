"""Analyze a preserved RAG retrieval detail file without calling models or databases.

This does not rerun retrieval. It recomputes metrics from the per-question rankings,
adds Hybrid-vs-single-route deltas and per-query win/tie/loss counts, and writes a
Markdown summary plus a machine-readable JSON file beside the source result.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval import metrics


K = 5
CONFIGS = {
    "纯向量": "vector_topk",
    "纯BM25": "bm25_topk",
    "混合": "hybrid_topk",
}
METRIC_NAMES = (f"Recall@{K}", f"Precision@{K}", "MRR", f"nDCG@{K}")


def per_query(ranked: list[str], gold: list[str]) -> dict[str, float]:
    return {
        f"Recall@{K}": metrics.recall_at_k(ranked, gold, K),
        f"Precision@{K}": metrics.precision_at_k(ranked, gold, K),
        "MRR": metrics.mrr(ranked, gold),
        f"nDCG@{K}": metrics.ndcg_at_k(ranked, gold, K),
    }


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def rounded(value: float) -> float:
    return round(value, 6)


def compare(rows: list[dict], left: str, right: str, metric: str) -> dict[str, int]:
    out = {"win": 0, "tie": 0, "loss": 0}
    for row in rows:
        delta = row["metrics"][left][metric] - row["metrics"][right][metric]
        if delta > 1e-12:
            out["win"] += 1
        elif delta < -1e-12:
            out["loss"] += 1
        else:
            out["tie"] += 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("details", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.details.read_text(encoding="utf-8"))
    items = payload["RAG 检索"]
    rows: list[dict] = []
    for item in items:
        row = {
            "question": item["question"],
            "gold": item["gold"],
            "rankings": {},
            "metrics": {},
        }
        for label, field in CONFIGS.items():
            ranked = item.get(field, [])[:K]
            row["rankings"][label] = ranked
            row["metrics"][label] = per_query(ranked, item["gold"])
        rows.append(row)

    aggregate = {
        label: {
            metric: rounded(avg([row["metrics"][label][metric] for row in rows]))
            for metric in METRIC_NAMES
        }
        for label in CONFIGS
    }
    deltas = {
        baseline: {
            metric: rounded(aggregate["混合"][metric] - aggregate[baseline][metric])
            for metric in METRIC_NAMES
        }
        for baseline in ("纯向量", "纯BM25")
    }
    wins = {
        baseline: {
            metric: compare(rows, "混合", baseline, metric)
            for metric in METRIC_NAMES
        }
        for baseline in ("纯向量", "纯BM25")
    }

    stem = args.details.stem.replace("details-", "")
    out_json = args.details.parent / f"retrieval-comparison-{stem}.json"
    out_md = args.details.parent / f"retrieval-comparison-{stem}.md"
    result = {
        "source_details": args.details.name,
        "note": "本报告从已保存的逐题召回结果重算，没有重新调用模型或数据库。",
        "query_count": len(rows),
        "k": K,
        "aggregate": aggregate,
        "hybrid_absolute_delta": deltas,
        "hybrid_per_query_win_tie_loss": wins,
        "details": rows,
    }
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# RAG 单路与混合检索对比 {stem}",
        "",
        "> 本报告从已保存的逐题召回结果重算，没有重新调用模型或数据库。",
        f"> 数据集：10 篇自建语料、{len(rows)} 道标注问题，K={K}。",
        "",
        "## 聚合结果",
        "",
        "| 配置 | Recall@5 | Precision@5 | MRR | nDCG@5 |",
        "|---|---:|---:|---:|---:|",
    ]
    for label in CONFIGS:
        a = aggregate[label]
        lines.append(
            f"| {label} | {a['Recall@5']:.4f} | {a['Precision@5']:.4f} | "
            f"{a['MRR']:.4f} | {a['nDCG@5']:.4f} |"
        )
    lines.extend(["", "## 混合检索的绝对差值", "", "| 对比基线 | Recall@5 | Precision@5 | MRR | nDCG@5 |", "|---|---:|---:|---:|---:|"])
    for baseline in ("纯向量", "纯BM25"):
        d = deltas[baseline]
        lines.append(
            f"| 混合 - {baseline} | {d['Recall@5']:+.4f} | {d['Precision@5']:+.4f} | "
            f"{d['MRR']:+.4f} | {d['nDCG@5']:+.4f} |"
        )
    lines.extend(["", "## 逐题胜 / 平 / 负", "", "| 对比基线 | 指标 | 胜 | 平 | 负 |", "|---|---|---:|---:|---:|"])
    for baseline in ("纯向量", "纯BM25"):
        for metric in METRIC_NAMES:
            w = wins[baseline][metric]
            lines.append(f"| 混合 vs {baseline} | {metric} | {w['win']} | {w['tie']} | {w['loss']} |")
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "- 这批数据上三路 Recall@5 和 Precision@5 相同，混合检索没有扩大 Top5 召回集合中的正确文档数。",
            "- 混合的 MRR 和 nDCG@5 比纯向量略高，表示它修正了少量排序；但它与纯 BM25 的聚合指标持平。",
            "- 因此不能宣称‘混合比最佳单路提升 10%–30%’。当前证据只支持‘混合匹配了 BM25 的排序质量，并比纯向量略好’。",
            "- 数据集只有 10 篇语料和 22 道问题，而且三路 Recall@5 已经饱和到 1.0。要验证混合检索的互补性，需要增加更多相似干扰文档、同义改写题、错误码/专名题、纯 BM25 容易漏的题和纯向量容易混淆的题。",
            "",
            "逐题排名、gold 和单题指标保存在同名 JSON 中。",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out_md)
    print(out_json)


if __name__ == "__main__":
    main()
