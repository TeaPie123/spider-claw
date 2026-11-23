"""
作用：提供无外部服务的任务链路测试替身。
流程：模拟持久化、队列、锁和沙箱 → 驱动真实任务服务与执行器 → 记录可验证的结果。
"""

import asyncio
import copy
import io
import tarfile
from contextlib import asynccontextmanager
from types import SimpleNamespace

from tasks.schemas import TaskRecord, TaskStatus


class MemoryStore:
    def __init__(self) -> None:
        self.records, self.keys = {}, {}

    async def create(self, task_id, tenant_id, payload, key):
        lookup = (tenant_id, key)
        if key is not None and lookup in self.keys:
            return self.records[self.keys[lookup]].model_copy(deep=True), False
        record = TaskRecord(
            task_id=task_id, tenant_id=tenant_id, status=TaskStatus.queued, request=copy.deepcopy(payload)
        )
        self.records[task_id] = record
        if key is not None:
            self.keys[lookup] = task_id
        return record.model_copy(deep=True), True

    async def get(self, task_id, tenant_id=None):
        record = self.records.get(task_id)
        return record.model_copy(deep=True) if record and (tenant_id is None or record.tenant_id == tenant_id) else None

    async def claim(self, task_id):
        record = self.records[task_id]
        if record.status not in {TaskStatus.queued, TaskStatus.running}:
            return False
        record.status = TaskStatus.running
        return True

    async def finish(self, task_id, status, result=None, error=None):
        record = self.records[task_id]
        record.status, record.result, record.error = status, result, error

    async def latest_success(self, thread_id):
        found = [
            r for r in self.records.values() if r.status == TaskStatus.succeeded and r.request["thread_id"] == thread_id
        ]
        return found[-1].model_copy(deep=True) if found else None

    async def dispatch_error(self, task_id):
        if self.records[task_id].status == TaskStatus.queued:
            self.records[task_id].error = "Waiting for queue recovery"

    async def notification(self, task_id, error=None):
        self.records[task_id].notified = error is None
        self.records[task_id].notification_error = error


class MemoryQueue:
    def __init__(self) -> None:
        self.items, self.fail = [], False

    async def enqueue(self, task_id):
        if self.fail:
            raise ConnectionError("queue down")
        if task_id not in self.items:
            self.items.append(task_id)


class MemoryRedis:
    def __init__(self) -> None:
        self.locks, self.counts = {}, {}

    def lock(self, name, **kwargs):
        return self.locks.setdefault(name, asyncio.Lock())

    async def eval(self, script, number, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def ping(self):
        return True

    async def enqueue_job(self, function, task_id, **kwargs):
        self.counts[f"{function}:{task_id}"] = self.counts.get(f"{function}:{task_id}", 0) + 1


class FakeManager:
    def __init__(self) -> None:
        self.opened, self.closed, self.restored = 0, 0, []
        self.export_error = None

    @asynccontextmanager
    async def open(self, project, previous_workspace=None):
        self.opened += 1
        self.restored.append(previous_workspace)
        try:
            yield SimpleNamespace(id="isolated-test-container", workdir="/home/agent/project")
        finally:
            self.closed += 1

    async def export(self, backend):
        if self.export_error:
            raise self.export_error
        content = b"VALUE = 2\n"
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            entry = tarfile.TarInfo("main.py")
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
        return b"diff --git a/main.py b/main.py\n+VALUE = 2\n", buffer.getvalue()


class FakeAgent:
    def __init__(self, error=None, delay=0) -> None:
        self.calls, self.error, self.delay = [], error, delay

    async def ainvoke(self, payload, config):
        self.calls.append((payload, config))
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return {"messages": [SimpleNamespace(content="Updated code; verification completed.")]}

    async def aget_state(self, config):
        return SimpleNamespace(config={"configurable": {"checkpoint_id": f"checkpoint-{len(self.calls)}"}})


def agent_factory(agent):
    async def build(settings, backend, checkpointer, semaphore):
        return agent

    return build
