"""
作用：验证通知重试与漏投任务恢复不会重跑已完成的代码任务。
流程：模拟渠道和队列异常 → 检查持久化状态 → 单独重试通知或重新投递任务编号。
"""

import pytest
from arq import Retry
from redis.exceptions import LockError

from tasks.schemas import TaskRequest, TaskStatus
from tasks.worker import notify, notify_task, process_task, recover_dispatch


@pytest.mark.asyncio
async def test_notification_failure_is_separate_from_execution(services):
    services.settings.feishu_reply_enabled = True
    task = await services.service.submit(
        TaskRequest(text="task", conversation_id="chat-1"), "tenant-a", reply_to="chat-1"
    )
    await services.store.finish(task.task_id, TaskStatus.succeeded, result={"reply": "done"})

    class Notifier:
        async def send(self, destination, text):
            raise ConnectionError("channel unavailable")

    ctx = dict(settings=services.settings, tasks=services.store, redis=services.redis, notifier=Notifier())
    with pytest.raises(Retry):
        await notify_task(ctx, task.task_id)
    record = await services.store.get(task.task_id)
    assert record.status == TaskStatus.succeeded
    assert record.notification_error
    await process_task(ctx, task.task_id)
    assert services.redis.counts[f"notify_task:{task.task_id}"] == 1


@pytest.mark.asyncio
async def test_completed_notification_is_not_sent_again(services):
    services.settings.feishu_reply_enabled = True
    task = await services.service.submit(TaskRequest(text="task"), "tenant-a", reply_to="chat")
    await services.store.finish(task.task_id, TaskStatus.succeeded, result={"reply": "done"})
    delivered = []

    class Notifier:
        async def send(self, destination, text):
            delivered.append(destination)

    ctx = dict(settings=services.settings, tasks=services.store, notifier=Notifier())
    assert await notify(ctx, await services.store.get(task.task_id))
    assert await notify(ctx, await services.store.get(task.task_id))
    assert delivered == ["chat"]


@pytest.mark.asyncio
async def test_recovery_enqueues_persisted_tasks(services):
    task = await services.service.submit(TaskRequest(text="task"), "tenant-a")

    async def pending():
        return [await services.store.get(task.task_id)]

    services.store.pending_dispatch = pending
    await recover_dispatch(dict(tasks=services.store, redis=services.redis))
    assert services.redis.counts[f"process_task:{task.task_id}"] == 1


@pytest.mark.asyncio
async def test_contended_lock_defers_execution_instead_of_losing_task(services):
    class ContendedRedis:
        def lock(self, *args, **kwargs):
            raise LockError("busy")

    task = await services.service.submit(TaskRequest(text="task"), "tenant-a")
    with pytest.raises(Retry):
        await process_task(dict(settings=services.settings, tasks=services.store, redis=ContendedRedis()), task.task_id)
    assert (await services.store.get(task.task_id)).status == TaskStatus.queued
