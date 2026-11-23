"""
作用：持久化统一任务及其状态变化。
流程：初始化任务表 → 幂等创建记录 → 原子领取任务 → 保存结果并按租户查询。
"""

from typing import Any

from psycopg.types.json import Jsonb

from tasks.schemas import TaskRecord, TaskStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS spider_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL,
    request JSONB NOT NULL,
    result JSONB,
    error TEXT,
    notified BOOLEAN NOT NULL DEFAULT FALSE,
    notification_error TEXT,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS spider_tasks_status_idx ON spider_tasks(status, updated_at);
ALTER TABLE spider_tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS spider_tasks_thread_completed_idx ON spider_tasks((request->>'thread_id'), completed_at DESC);
"""


class TaskStore:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def setup(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(SCHEMA, prepare=False)

    async def create(self, task_id: str, tenant_id: str, payload: dict, key: str | None) -> tuple[TaskRecord, bool]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO spider_tasks(task_id,tenant_id,idempotency_key,status,request) "
                "VALUES (%s,%s,%s,'queued',%s) ON CONFLICT (tenant_id,idempotency_key) "
                "DO NOTHING RETURNING *",
                (task_id, tenant_id, key, Jsonb(payload)),
            )
            row = await cur.fetchone()
            if row:
                return TaskRecord(**row), True
            cur = await conn.execute(
                "SELECT * FROM spider_tasks WHERE tenant_id=%s AND idempotency_key=%s",
                (tenant_id, key),
            )
            row = await cur.fetchone()
        if row is None:
            raise RuntimeError("Unable to create or retrieve task")
        return TaskRecord(**row), False

    async def get(self, task_id: str, tenant_id: str | None = None) -> TaskRecord | None:
        query, params = "SELECT * FROM spider_tasks WHERE task_id=%s", [task_id]
        if tenant_id is not None:
            query += " AND tenant_id=%s"
            params.append(tenant_id)
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, params)
            row = await cur.fetchone()
        return TaskRecord(**row) if row else None

    async def claim(self, task_id: str) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE spider_tasks SET status='running',error=NULL,updated_at=NOW() "
                "WHERE task_id=%s AND status IN ('queued','running') RETURNING task_id",
                (task_id,),
            )
            return await cur.fetchone() is not None

    async def latest_success(self, thread_id: str) -> TaskRecord | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM spider_tasks WHERE status='succeeded' AND request->>'thread_id'=%s "
                "ORDER BY completed_at DESC NULLS LAST,created_at DESC LIMIT 1",
                (thread_id,),
            )
            row = await cur.fetchone()
        return TaskRecord(**row) if row else None

    async def finish(
        self, task_id: str, status: TaskStatus, result: dict | None = None, error: str | None = None
    ) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE spider_tasks SET status=%s,result=%s,error=%s,updated_at=NOW(),completed_at=NOW() WHERE task_id=%s",
                (status.value, Jsonb(result) if result is not None else None, error, task_id),
            )

    async def dispatch_error(self, task_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE spider_tasks SET error='Waiting for queue recovery',updated_at=NOW() "
                "WHERE task_id=%s AND status='queued'",
                (task_id,),
            )

    async def notification(self, task_id: str, error: str | None = None) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE spider_tasks SET notified=%s,notification_error=%s,updated_at=NOW() WHERE task_id=%s",
                (error is None, error, task_id),
            )

    async def pending_dispatch(self) -> list[TaskRecord]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM spider_tasks WHERE status='queued' AND created_at < NOW() - INTERVAL '30 seconds' "
                "ORDER BY created_at LIMIT 100"
            )
            return [TaskRecord(**row) for row in await cur.fetchall()]
