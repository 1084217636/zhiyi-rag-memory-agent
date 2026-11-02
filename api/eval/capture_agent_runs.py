"""Capture actual chat SSE events for later human labeling.

Requires a dedicated evaluation account with the casebook corpus already
uploaded. By default only RAG cases run with memory disabled, so the questions
do not create new long-term memories in the evaluation account.
"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from eval.grounded_metrics import read_jsonl


def parse_sse_block(lines: list[str]) -> tuple[str, dict] | None:
    event = "message"
    data = []
    for line in lines:
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    if not data:
        return None
    payload = json.loads("\n".join(data))
    if not isinstance(payload, dict):
        raise ValueError("SSE data must be an object")
    return event, payload


async def capture_case(client: httpx.AsyncClient, case: dict, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        "/api/conversations", headers=headers,
        json={"title": f"eval-{case['case_id']}"},
    )
    created.raise_for_status()
    payload = created.json()
    if payload.get("code") != 0 or not payload.get("data", {}).get("id"):
        raise RuntimeError(f"create conversation failed for {case['case_id']}")
    conversation_id = payload["data"]["id"]
    events: list[dict] = []
    buffer: list[str] = []
    started = time.perf_counter()
    request = {
        "conversation_id": conversation_id,
        "message": case["question"],
        "enable_knowledge": case["case_type"] == "rag",
        "enable_memory": case["case_type"] == "memory",
        "enable_web_search": False,
    }
    async with client.stream("POST", "/api/chat/stream", headers=headers, json=request) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line:
                buffer.append(line)
            elif buffer:
                parsed = parse_sse_block(buffer)
                if parsed:
                    event, data = parsed
                    events.append({"event": event, "data": data})
                buffer = []
        if buffer:
            parsed = parse_sse_block(buffer)
            if parsed:
                event, data = parsed
                events.append({"event": event, "data": data})
    answer = "".join(str(e["data"].get("text", "")) for e in events if e["event"] == "token")
    citations = [item for e in events if e["event"] == "citation"
                 for item in e["data"].get("citations", [])]
    meta = next((e["data"] for e in events if e["event"] == "meta"), {})
    return {
        "case_id": case["case_id"], "case_type": case["case_type"],
        "question": case["question"], "conversation_id": conversation_id,
        "trace_id": meta.get("trace_id"), "answer": answer,
        "citations": citations,
        "called_tools": [e["data"].get("tool") for e in events
                         if e["event"] == "tool_start"],
        "tool_events": [e for e in events if e["event"] in {"tool_start", "tool_result"}],
        "errors": [e["data"] for e in events if e["event"] == "error"],
        "done": any(e["event"] == "done" for e in events),
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


async def run(args: argparse.Namespace) -> None:
    token = os.getenv(args.token_env)
    if not token:
        raise RuntimeError(f"missing token in environment variable {args.token_env}")
    cases = read_jsonl(args.casebook)
    selected = [case for case in cases
                if case.get("case_type") == "rag"
                or (args.include_manual and case.get("case_type") == "memory")]
    if args.limit:
        selected = selected[:args.limit]
    if not selected:
        raise ValueError("no runnable cases selected")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create prevents accidental loss of a previous expensive run.
    with args.output.open("x", encoding="utf-8") as output:
        async with httpx.AsyncClient(base_url=args.base_url, timeout=240) as client:
            for case in selected:
                result = await capture_case(client, case, token)
                output.write(json.dumps(result, ensure_ascii=False) + "\n")
                output.flush()
                print(f"{case['case_id']}: done={result['done']} errors={len(result['errors'])}")
    print(f"captured {len(selected)} runs in {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="isolated evaluation API origin")
    parser.add_argument("--casebook", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--token-env", default="ZHIYI_EVAL_TOKEN")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--include-manual", action="store_true",
                        help="also run memory cases; use a fresh isolated account per version")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
