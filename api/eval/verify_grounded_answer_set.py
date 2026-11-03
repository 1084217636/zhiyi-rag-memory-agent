"""Run a frozen synthetic answer set through the real HTTP Agent path.

Twenty documents each define one value and explicitly omit a second value.
This tests grounded answers and abstentions with source IDs. The wording is
easy and synthetic, so scores are regression evidence, not general accuracy.
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from eval.verify_agent_boundaries import _account, _chat, _upload


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * fraction
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 2)


def run(base: str) -> dict:
    client = _account(base)
    rows: list[dict] = []
    try:
        documents = []
        for number in range(1, 21):
            name = f"合成观测项目 Q{number:02d}"
            value = str(4000 + number)
            doc_id = _upload(
                client,
                f"# {name}\n本记录的主样本读数是 {value}。"
                "本记录没有记录备用样本的读数。\n",
                f"synthetic-q{number:02d}.md",
            )
            documents.append((name, value, doc_id))

        for index, (name, value, doc_id) in enumerate(documents, 1):
            result = _chat(client, f"我上传的《{name}》中，主样本读数是多少？请注明来源。")
            cited = {c.get("source_id") for c in result["citations"]}
            rows.append({
                "case_id": f"answer-{index:02d}", "type": "answerable",
                "passed": (
                    value in result["answer"] and doc_id in cited and
                    result["metadata"].get("verification", {}).get("status") == "verified" and
                    "error" not in result["events"]
                ),
                "expected_value": value, "answer": result["answer"],
                "cited_expected_doc": doc_id in cited,
                "verification": result["metadata"].get("verification"),
                "latency_ms": result["latency_ms"],
            })
            print(f"{rows[-1]['case_id']}: {'PASS' if rows[-1]['passed'] else 'FAIL'}", flush=True)
            time.sleep(2)  # Stay below the development limit of 12 Agent runs/minute.

        for index, (name, _value, _doc_id) in enumerate(documents[:10], 1):
            result = _chat(client, f"我上传的《{name}》中，备用样本的读数是多少？")
            answer = result["answer"]
            rows.append({
                "case_id": f"missing-{index:02d}", "type": "unanswerable",
                "passed": (
                    any(phrase in answer for phrase in
                        ("没有记录", "未记录", "无法给出", "无法确认", "没有提供")) and
                    "error" not in result["events"] and
                    result["metadata"].get("verification", {}).get("status") in
                        {"verified", "insufficient_evidence", "unsupported"}
                ),
                "answer": answer,
                "verification": result["metadata"].get("verification"),
                "latency_ms": result["latency_ms"],
            })
            print(f"{rows[-1]['case_id']}: {'PASS' if rows[-1]['passed'] else 'FAIL'}", flush=True)
            time.sleep(2)

        answerable = [row for row in rows if row["type"] == "answerable"]
        unanswerable = [row for row in rows if row["type"] == "unanswerable"]
        latencies = [row["latency_ms"] for row in rows]
        return {
            "method": "fresh synthetic user; 20 isolated uploaded Markdown sources; real Celery/ES/Agent/Verifier/SSE; easy title-bearing questions",
            "answerable_cases": len(answerable),
            "answerable_passed": sum(row["passed"] for row in answerable),
            "unanswerable_cases": len(unanswerable),
            "unanswerable_passed": sum(row["passed"] for row in unanswerable),
            "answerable_citation_hits": sum(row["cited_expected_doc"] for row in answerable),
            "latency_p50_ms": _percentile(latencies, 0.5),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "mean_latency_ms": round(statistics.mean(latencies), 2),
            "rows": rows,
        }
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8008")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results" / "grounded-answers-30.json")
    args = parser.parse_args()
    started = time.perf_counter()
    report = run(args.base)
    report["wall_time_seconds"] = round(time.perf_counter() - started, 2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, indent=2))
    if report["answerable_passed"] != 20 or report["unanswerable_passed"] != 10:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
