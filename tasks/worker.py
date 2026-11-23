"""
作用：作为唯一执行入口消费队列中的研发任务。
流程：打开资源 → 按任务和会话加锁 → 恢复最近成功快照 → 执行并更新状态 → 回复渠道 → 释放资源。
"""

import asyncio
import time
from contextlib import AsyncExitStack

from arq import Retry, cron
from arq.connections import RedisSettings
from arq.worker import func
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from prometheus_client import start_http_server
from redis.exceptions import LockError

from agent.dispatcher import TaskExecutor, artifact_directory
from infra.db import build_pg_pool
from infra.logging import get_logger
from infra.settings import configure_tracing, get_settings
from obs.metrics import TASK_DURATION, TASKS
from tasks.schemas import TaskStatus
from tasks.store import TaskStore


async def notify(ctx: dict, record) -> bool:
    if record.notified or not record.request.get("reply_to") or not ctx["settings"].feishu_reply_enabled:
        return True
    from channels.feishu import FeishuChannel

    text = (record.result or {}).get("reply") if record.status == TaskStatus.succeeded else record.error
    try:
        channel = ctx.get("notifier") or FeishuChannel(ctx["settings"])
        await channel.send(record.request["reply_to"], f"任务 {record.task_id}：{text or record.status.value}")
    except Exception as exc:
        await ctx["tasks"].notification(record.task_id, f"Channel delivery failed: {type(exc).__name__}")
        return False
    else:
        await ctx["tasks"].notification(record.task_id)
        return True


async def notify_task(ctx: dict, task_id: str) -> None:
    record = await ctx["tasks"].get(task_id)
    if record is not None and not await notify(ctx, record):
        raise Retry(defer=min(300, 10 * 2 ** ctx.get("job_try", 1)))


async def recover_dispatch(ctx: dict) -> None:
    for record in await ctx["tasks"].pending_dispatch():
        await ctx["redis"].enqueue_job("process_task", record.task_id, _job_id=record.task_id)


async def _process_task(ctx: dict, task_id: str) -> dict:
    settings, store, redis = ctx["settings"], ctx["tasks"], ctx["redis"]
    async with redis.lock(f"spider:task:{task_id}", timeout=settings.task_timeout * 2 + 360, blocking_timeout=5):
        record = await store.get(task_id)
        if record is None:
            raise ValueError("Queued task no longer exists")
        if record.status in {TaskStatus.succeeded, TaskStatus.failed}:
            if not await notify(ctx, record):
                await ctx["redis"].enqueue_job("notify_task", task_id, _job_id=f"notify:{task_id}", _defer_by=10)
            return {"task_id": task_id, "status": record.status.value}
        thread = record.request["thread_id"]
        async with redis.lock(
            f"spider:session:{thread}",
            timeout=settings.task_timeout + 120,
            blocking_timeout=settings.task_timeout + 120,
        ):
            if not await store.claim(task_id):
                return {"task_id": task_id, "status": "already_finished"}
            start = time.monotonic()
            try:
                previous = await store.latest_success(thread)
                workspace = None
                if previous is not None:
                    workspace = artifact_directory(settings, record.tenant_id, previous.task_id) / "workspace.tar.gz"
                    if not workspace.is_file():
                        raise RuntimeError("Previous conversation workspace is unavailable")
                async with asyncio.timeout(settings.task_timeout):
                    result = await ctx["executor"].execute(record, workspace, previous.result if previous else None)
            except asyncio.CancelledError:
                await asyncio.shield(store.finish(task_id, TaskStatus.failed, error="Worker execution was cancelled"))
                raise
            except TimeoutError:
                await store.finish(task_id, TaskStatus.failed, error="Task execution timed out")
            except Exception as exc:
                get_logger().error("Task {} failed: {}", task_id, type(exc).__name__)
                await store.finish(task_id, TaskStatus.failed, error=f"Task execution failed: {type(exc).__name__}")
            else:
                await store.finish(task_id, TaskStatus.succeeded, result=result)
            finally:
                TASK_DURATION.observe(time.monotonic() - start)
        finished = await store.get(task_id)
        TASKS.labels(finished.status.value).inc()
        if not await notify(ctx, finished):
            await ctx["redis"].enqueue_job("notify_task", task_id, _job_id=f"notify:{task_id}", _defer_by=10)
        return {"task_id": task_id, "status": finished.status.value}


async def process_task(ctx: dict, task_id: str) -> dict:
    try:
        return await _process_task(ctx, task_id)
    except LockError as exc:
        raise Retry(defer=5) from exc


async def on_startup(ctx: dict) -> None:
    settings = get_settings()
    configure_tracing(settings)
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        pool = build_pg_pool(settings)
        stack.push_async_callback(pool.close)
        await pool.open(wait=True)
        store = TaskStore(pool)
        await store.setup()
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        ctx.update(settings=settings, tasks=store, executor=TaskExecutor(settings, checkpointer), resources=stack)
        if settings.worker_metrics_port:
            server, thread = start_http_server(settings.worker_metrics_port)
            stack.callback(server.server_close)
            stack.callback(server.shutdown)
    except BaseException:
        await stack.aclose()
        raise


async def on_shutdown(ctx: dict) -> None:
    await ctx["resources"].aclose()


class WorkerSettings:
    functions = [process_task, func(notify_task, max_tries=5)]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = get_settings().sandbox_pool_size
    job_timeout = get_settings().task_timeout * 2 + 360
    max_tries = 1000
    keep_result = 3600
    cron_jobs = [cron(recover_dispatch, second={0, 30})]
