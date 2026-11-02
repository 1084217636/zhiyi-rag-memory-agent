"""Small Prometheus text exposition for low-cardinality HTTP service metrics."""
import threading
import time
from collections import defaultdict


class RequestMetrics:
    def __init__(self, app=None, registry=None):
        self.app = app
        self.registry = registry or self
        if registry is None:
            self._lock = threading.Lock()
            self._counts = defaultdict(int)
            self._duration_buckets = defaultdict(lambda: [0] * 7)
            self._duration_count = defaultdict(int)
            self._duration_sum = defaultdict(float)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in {
            "/metrics", "/api/metrics", "/health", "/api/health"
        }:
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def tracked_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        finally:
            route = scope.get("route")
            route_name = getattr(route, "path", "unmatched")
            labels = (scope.get("method", "GET"), route_name, f"{status // 100}xx")
            elapsed = time.perf_counter() - started
            with self.registry._lock:
                self.registry._counts[labels] += 1
                self.registry._duration_count[labels] += 1
                self.registry._duration_sum[labels] += elapsed
                for index, bound in enumerate((0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)):
                    if elapsed <= bound:
                        self.registry._duration_buckets[labels][index] += 1

    def render(self) -> str:
        registry = self.registry
        with registry._lock:
            counts = dict(registry._counts)
            bucket_counts = {key: list(values) for key, values in registry._duration_buckets.items()}
            duration_counts = dict(registry._duration_count)
            duration_sums = dict(registry._duration_sum)
        lines = [
            "# HELP zhiyi_http_requests_total Completed HTTP requests by method, route template and status class.",
            "# TYPE zhiyi_http_requests_total counter",
        ]
        for (method, route, status), value in sorted(counts.items()):
            labels = f'method="{_escape(method)}",route="{_escape(route)}",status="{status}"'
            lines.append(f"zhiyi_http_requests_total{{{labels}}} {value}")
        lines += [
            "# HELP zhiyi_http_request_duration_seconds HTTP request latency histogram.",
            "# TYPE zhiyi_http_request_duration_seconds histogram",
        ]
        bounds = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
        for key, values in sorted(bucket_counts.items()):
            method, route, status = key
            labels = f'method="{_escape(method)}",route="{_escape(route)}",status="{status}"'
            for index, bound in enumerate(bounds):
                count = values[index]
                lines.append(f'zhiyi_http_request_duration_seconds_bucket{{{labels},le="{bound}"}} {count}')
            lines.append(f'zhiyi_http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {duration_counts[key]}')
            lines.append(f"zhiyi_http_request_duration_seconds_count{{{labels}}} {duration_counts[key]}")
            lines.append(f"zhiyi_http_request_duration_seconds_sum{{{labels}}} {duration_sums[key]:.9f}")
        return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
