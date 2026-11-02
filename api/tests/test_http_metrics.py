from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.http_metrics import RequestMetrics
from app.controllers.ops_controller import router


def test_metrics_uses_route_templates_and_excludes_scrapes():
    app = FastAPI()
    metrics = RequestMetrics()
    app.add_middleware(RequestMetrics, registry=metrics)
    app.state.request_metrics = metrics
    app.include_router(router)

    @app.get("/items/{item_id}")
    async def item(item_id: str):
        return {"id": item_id}

    client = TestClient(app)
    assert client.get("/items/abc").status_code == 200
    body = client.get("/metrics").text
    assert 'route="/items/{item_id}"' in body
    assert 'method="GET"' in body
    assert "zhiyi_http_request_duration_seconds_bucket" in body
    assert client.get("/metrics").text == body
