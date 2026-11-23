"""
作用：组装 API、依赖资源和统一异常处理。
流程：打开数据库与队列 → 注入任务服务 → 挂载路由 → 退出时释放所有连接。
"""

from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from api.routes.tasks import router as task_router
from channels.webhook import router as webhook_router
from infra.db import build_pg_pool
from infra.logging import get_logger
from infra.settings import Settings, get_settings
from obs.metrics import HTTPMetrics
from tasks.queue import TaskQueue, open_queue
from tasks.service import AccessDenied, QueueUnavailable, TaskNotFound, TaskService
from tasks.store import TaskStore


def create_app(settings: Settings | None = None, service=None, redis=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if service is not None:
            app.state.tasks, app.state.redis = service, redis
            yield
            return
        async with AsyncExitStack() as stack:
            pool = build_pg_pool(settings)
            stack.push_async_callback(pool.close)
            await pool.open(wait=True)
            store = TaskStore(pool)
            await store.setup()
            queue_redis = await open_queue(settings)
            stack.push_async_callback(queue_redis.aclose)
            app.state.redis = queue_redis
            app.state.tasks = TaskService(store, TaskQueue(queue_redis), settings)
            yield

    application = FastAPI(title="spider claw API", version="0.2.0", lifespan=lifespan)
    application.state.settings = settings
    application.add_middleware(HTTPMetrics)
    application.include_router(task_router)
    application.include_router(webhook_router)

    @application.exception_handler(AccessDenied)
    async def forbidden(request: Request, exc: AccessDenied):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @application.exception_handler(TaskNotFound)
    async def missing(request: Request, exc: TaskNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(QueueUnavailable)
    async def queue_error(request: Request, exc: QueueUnavailable):
        return JSONResponse(status_code=503, content={"detail": str(exc), "task_id": exc.task_id, "status": "queued"})

    @application.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        get_logger().error("API request failed: {}", type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @application.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @application.get("/readyz")
    async def readyz(request: Request):
        try:
            if not await request.app.state.redis.ping():
                raise RuntimeError("Redis unavailable")
            pool = getattr(request.app.state.tasks.store, "pool", None)
            if pool is not None:
                async with pool.connection() as conn:
                    await conn.execute("SELECT 1")
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ready"}

    @application.get("/metrics")
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return application


app = create_app()
