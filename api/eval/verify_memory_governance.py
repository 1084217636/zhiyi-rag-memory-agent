"""Exercise conflict confirmation, audit and source deletion with an isolated user."""
import time
import uuid

import httpx


def payload(response):
    response.raise_for_status()
    body = response.json()
    if body.get("code") != 0:
        raise AssertionError(body)
    return body["data"]


def wait_source(client, memory_id):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        row = payload(client.get(f"/api/memories/{memory_id}"))
        if row["status"] == "done":
            return row
        if row["status"] == "failed":
            raise AssertionError(f"memory extraction failed: {row['error_msg']}")
        time.sleep(2)
    raise TimeoutError(memory_id)


def main():
    name = "governance" + uuid.uuid4().hex[:10]
    with httpx.Client(base_url="http://127.0.0.1:8008", timeout=30) as client:
        payload(client.post("/api/auth/register", json={"username": name, "password": "Local-Eval-2026!"}))
        token = payload(client.post("/api/auth/login", json={
            "username": name, "password": "Local-Eval-2026!"
        }))["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        old = payload(client.post("/api/memories/remember", json={
            "text": "我目前在杭州工作。"
        }))
        old_done = wait_source(client, old["id"])
        new = payload(client.post("/api/memories/remember", json={
            "text": "我现在在深圳工作。"
        }))
        new_done = wait_source(client, new["id"])
        status = payload(client.get("/api/memories/policy/status"))
        operations = payload(client.get("/api/memories/policy/operations"))
        print("old/new extraction", old_done["graph_stats"], new_done["graph_stats"])
        print("policy", status["counts"], "pending", status["pending"])
        print("audit actions", [x["action"] for x in operations])
        pending = [row for row in status["pending"] if "深圳" in row["statement"]]
        assert pending, "new current city should require confirmation"
        payload(client.post(f"/api/memories/statements/{pending[0]['id']}/resolve", json={
            "action": "replace"
        }))
        after = payload(client.get("/api/memories/policy/status"))
        assert after["counts"].get("superseded", 0) >= 1, after
        payload(client.delete(f"/api/memories/{new['id']}"))
        deleted = payload(client.get(f"/api/memories/{new['id']}"))
        assert deleted["status"] == "deleted" and not deleted["raw_text"], deleted
        after_delete = payload(client.get("/api/memories/policy/status"))
        operations = payload(client.get("/api/memories/policy/operations"))
        assert any(x["action"] == "DELETE_SOURCE" for x in operations)
        assert not any("深圳" in row["statement"] for row in after_delete["pending"])
        assert after_delete["counts"].get("active", 0) >= 1, after_delete
        from app.tasks.memory import extract_memory_task
        extract_memory_task.apply_async(args=(new["id"],), queue="memory")
        time.sleep(3)
        replay = payload(client.get(f"/api/memories/{new['id']}"))
        assert replay["status"] == "deleted" and not replay["raw_text"], replay
        print("PASS conflict → user replacement → audit → source deletion", after_delete["counts"])


if __name__ == "__main__":
    main()
