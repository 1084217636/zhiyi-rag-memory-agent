"""Fresh-user cross-conversation memory acceptance through the real API/Celery/Neo4j."""
import json
import time
import uuid

import httpx


def data(response):
    response.raise_for_status()
    body = response.json()
    if body.get("code") != 0:
        raise AssertionError(body)
    return body["data"]


def chat(client, conversation_id, message):
    events = []
    with client.stream("POST", "/api/chat/stream", timeout=240, json={
        "conversation_id": conversation_id,
        "message": message,
        "enable_knowledge": False,
        "enable_memory": True,
        "enable_web_search": False,
    }) as response:
        response.raise_for_status()
        name = None
        for line in response.iter_lines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line[5:].strip())))
    kinds = [name for name, _ in events]
    assert "done" in kinds and "error" not in kinds, kinds
    return "".join(row.get("text", "") for name, row in events if name == "token"), kinds


def main():
    username = "evalmemory" + uuid.uuid4().hex[:10]
    password = "Local-Eval-2026!"
    with httpx.Client(base_url="http://127.0.0.1:8008", timeout=30) as client:
        data(client.post("/api/auth/register", json={"username": username, "password": password}))
        token = data(client.post("/api/auth/login", json={"username": username, "password": password}))["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        first = data(client.post("/api/conversations", json={"title": "记忆写入验收"}))
        chat(client, first["id"], "我正在研究 WiFi CSI，用它分析人体动作。请简单回复收到。")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            hits = data(client.post("/api/memories/search", json={"query": "我研究什么方向？", "top_k": 10}))
            if any("csi" in json.dumps(hit, ensure_ascii=False).casefold() for hit in hits):
                break
            time.sleep(2)
        else:
            raise AssertionError("Celery memory extraction did not produce a CSI fact")
        second = data(client.post("/api/conversations", json={"title": "跨会话召回验收"}))
        answer, kinds = chat(client, second["id"], "我之前告诉你我正在研究什么？请简短回答。")
        assert "csi" in answer.casefold(), answer
        print("PASS first conversation → background fact → Neo4j search → new conversation answer", {
            "retrieval_hits": len(hits), "sse_events": kinds,
        })


if __name__ == "__main__":
    main()
