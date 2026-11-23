"""
作用：记录 HTTP 与任务执行的关键指标。
流程：请求结束记录路由状态与耗时 → Worker 记录任务状态与执行耗时 → Prometheus 抓取。
"""

import time

from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware

HTTP_REQUESTS = Counter("spider_http_requests_total", "HTTP responses", ["method", "route", "status"])
HTTP_DURATION = Histogram("spider_http_duration_seconds", "HTTP latency", ["route"])
TASKS = Counter("spider_tasks_total", "Finished tasks", ["status"])
TASK_DURATION = Histogram(
    "spider_task_duration_seconds", "Task execution latency", buckets=(1, 5, 15, 30, 60, 120, 300, 900)
)


class HTTPMetrics(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start, status = time.monotonic(), 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_DURATION.labels(route).observe(time.monotonic() - start)
