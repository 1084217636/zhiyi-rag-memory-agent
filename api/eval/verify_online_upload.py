"""Real API → Celery → PostgreSQL/Elasticsearch upload acceptance on a fresh user.

Run with the API and worker using the same Redis broker. Creates isolated demo
records and never edits another user's documents or evaluation namespace.
"""
import argparse
import json
import time
import uuid
from pathlib import Path

import httpx


def unpack(response: httpx.Response):
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise AssertionError(payload)
    return payload.get("data")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8008")
    parser.add_argument("--file", type=Path, default=Path(__file__).parent / "fixtures/corpus_100/wiki-001.md")
    parser.add_argument("--chat", action="store_true", help="also exercise live SSE Agent answering")
    args = parser.parse_args()
    username = "evalupload" + uuid.uuid4().hex[:10]
    password = "Local-Eval-2026!"
    with httpx.Client(base_url=args.base, timeout=30) as client:
        unpack(client.post("/api/auth/register", json={"username": username, "password": password}))
        token = unpack(client.post("/api/auth/login", json={"username": username, "password": password}))["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        with args.file.open("rb") as source:
            doc = unpack(client.post("/api/documents/upload", files={"file": (args.file.name, source, "text/markdown")}))
        document_id = doc["id"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            status = unpack(client.get(f"/api/documents/{document_id}/status"))
            if status["status"] == "done":
                break
            if status["status"] in {"failed", "error"}:
                raise AssertionError(f"parse failed: {status}")
            time.sleep(1)
        else:
            raise AssertionError(f"document did not finish parsing: {status}")
        hits = unpack(client.post("/api/documents/search", json={"query": "艾萨克·牛顿有什么贡献？", "top_k": 5}))
        if isinstance(hits, dict):
            hits = hits.get("hits", hits.get("items", []))
        assert any(str(hit.get("source_id")) == document_id for hit in hits), hits
        print("PASS register/login → HTTP upload → Celery parse → indexed search", {
            "document_id": document_id, "status": status["status"], "search_hits": len(hits),
        })
        if args.chat:
            conversation = unpack(client.post("/api/conversations", json={"title": "在线上传验收"}))
            events = []
            with client.stream("POST", "/api/chat/stream", timeout=240, json={
                "conversation_id": conversation["id"],
                "message": "请根据我刚上传的艾萨克·牛顿文档，概括他的主要贡献，并附上文档引用。",
                "enable_knowledge": True,
                "enable_memory": False,
                "enable_web_search": False,
            }) as response:
                response.raise_for_status()
                event_name = None
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        events.append((event_name, json.loads(line[5:].strip())))
            kinds = [name for name, _ in events]
            assert "done" in kinds and "error" not in kinds, kinds
            assert "tool_start" in kinds and "citation" in kinds, kinds
            answer = "".join(item.get("text", "") for name, item in events if name == "token")
            citations = [citation for name, item in events if name == "citation"
                         for citation in item.get("citations", [])]
            artifact = unpack(client.post("/api/research/artifacts", json={
                "title": "牛顿文档回答快照", "content": answer,
                "conversation_id": conversation["id"], "citations": citations,
            }))
            updated = unpack(client.put(f"/api/research/artifacts/{artifact['id']}", json={
                "title": "牛顿文档回答快照（核对）", "content": answer,
                "expected_version": artifact["version"],
            }))
            assert updated["version"] == 2
            assert any(item["id"] == artifact["id"] for item in unpack(client.get("/api/research/artifacts")))
            print("PASS live Agent knowledge tool → evidence citation → SSE completion", {
                "events": kinds, "conversation_id": conversation["id"],
                "artifact_version": updated["version"],
            })


if __name__ == "__main__":
    main()
