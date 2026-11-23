"""
作用：为可用的 Docker 与专用 Postgres 提供真实集成检查。
流程：显式启用集成环境 → 验证容器执行与回收或数据库任务状态 → 缺少服务时跳过。
"""

import os
from pathlib import Path
from uuid import uuid4

import pytest

from infra.db import build_pg_pool
from sandbox.docker_manager import DockerManager
from tasks.schemas import TaskStatus
from tasks.store import TaskStore


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("SPIDER_TEST_DOCKER") != "1", reason="Requires an explicitly enabled Docker environment"
)
async def test_real_docker_snapshot_and_patch(settings):
    manager = DockerManager(settings)
    async with manager.open(Path(settings.projects["default"])) as backend:
        uploaded = await backend.aupload_files([(f"{backend.workdir}/main.py", b"VALUE = 2\n")])
        assert uploaded[0].error is None
        command = await backend.aexecute("python -c 'assert 1 + 1 == 2'")
        assert command.exit_code == 0
        patch, workspace = await manager.export(backend)
        assert b"+VALUE = 2" in patch and workspace


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("SPIDER_TEST_DATABASE_URL"), reason="Requires a dedicated test Postgres database"
)
async def test_real_postgres_task_lifecycle(settings):
    settings.database_url = os.environ["SPIDER_TEST_DATABASE_URL"]
    pool = build_pg_pool(settings)
    await pool.open(wait=True)
    task_id, tenant = uuid4().hex, f"test-{uuid4().hex}"
    store = TaskStore(pool)
    try:
        await store.setup()
        record, created = await store.create(task_id, tenant, {"thread_id": "test", "project_id": "default"}, "key")
        assert created and record.status == TaskStatus.queued
        again, created = await store.create(uuid4().hex, tenant, record.request, "key")
        assert not created and again.task_id == task_id
        assert await store.claim(task_id)
        await store.finish(task_id, TaskStatus.succeeded, result={"reply": "ok"})
        assert (await store.get(task_id, tenant)).result == {"reply": "ok"}
        assert await store.get(task_id, "other-tenant") is None
    finally:
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM spider_tasks WHERE tenant_id=%s", (tenant,))
        await pool.close()
