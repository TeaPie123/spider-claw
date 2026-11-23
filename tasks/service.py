"""
作用：统一处理任务提交、项目授权、幂等和结果访问。
流程：验证项目权限 → 持久化任务 → 投递队列 → 按租户读取状态和产物。
"""

import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from channels.session import thread_id_for
from infra.settings import Settings
from tasks.schemas import TaskRecord, TaskRequest, TaskStatus


class AccessDenied(ValueError):
    pass


class TaskNotFound(ValueError):
    pass


class QueueUnavailable(RuntimeError):
    def __init__(self, task_id: str) -> None:
        super().__init__("Task is persisted and awaiting queue recovery")
        self.task_id = task_id


class TaskService:
    def __init__(self, store: Any, queue: Any, settings: Settings) -> None:
        self.store, self.queue, self.settings = store, queue, settings

    async def submit(
        self, request: TaskRequest, tenant_id: str, idempotency_key: str | None = None, reply_to: str | None = None
    ) -> TaskRecord:
        allowed = self.settings.tenant_projects.get(tenant_id, [])
        if request.project_id not in self.settings.projects or request.project_id not in allowed:
            raise AccessDenied("Project access denied")
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 200:
            raise ValueError("Idempotency-Key must contain 1 to 200 characters")
        payload = request.model_dump()
        if idempotency_key and "conversation_id" not in request.model_fields_set:
            payload["conversation_id"] = hashlib.sha256(f"{tenant_id}:{idempotency_key}".encode()).hexdigest()
        payload["thread_id"] = thread_id_for(tenant_id, request.project_id, request.channel, payload["conversation_id"])
        payload["reply_to"] = reply_to
        record, created = await self.store.create(uuid4().hex, tenant_id, payload, idempotency_key)
        if not created:
            if record.request != payload:
                raise ValueError("Idempotency-Key already belongs to a different request")
            if record.status != TaskStatus.queued:
                return record
        try:
            await self.queue.enqueue(record.task_id)
        except Exception as exc:
            await self.store.dispatch_error(record.task_id)
            raise QueueUnavailable(record.task_id) from exc
        return record

    async def get(self, task_id: str, tenant_id: str) -> TaskRecord:
        record = await self.store.get(task_id, tenant_id)
        if record is None:
            raise TaskNotFound("Task not found")
        return record

    async def artifact(self, task_id: str, tenant_id: str, name: str) -> Path:
        record = await self.get(task_id, tenant_id)
        if name not in {"changes.patch", "workspace.tar.gz", "summary.json"} or record.status != TaskStatus.succeeded:
            raise TaskNotFound("Artifact not found")
        try:
            UUID(record.task_id)
        except ValueError as exc:
            raise TaskNotFound("Artifact not found") from exc
        bucket = hashlib.sha256(tenant_id.encode()).hexdigest()[:24]
        root = self.settings.artifact_root.resolve()
        path = (root / bucket / record.task_id / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise TaskNotFound("Artifact not found")
        return path
