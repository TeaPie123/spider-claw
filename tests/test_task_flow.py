"""
作用：验证统一 API、队列、Worker、执行器和产物访问的完整协作。
流程：提交任务 → 消费队列 → 运行隔离执行器 → 查询结果与补丁 → 验证会话快照和租户隔离。
"""

import asyncio

import httpx
import pytest

from agent.dispatcher import TaskExecutor
from api.app import create_app
from tasks.schemas import TaskRequest, TaskStatus, task_view
from tasks.worker import process_task
from tests.support import FakeAgent, FakeManager, agent_factory


@pytest.mark.asyncio
async def test_api_queue_worker_artifact_roundtrip(services):
    manager, agent = FakeManager(), FakeAgent()
    executor = TaskExecutor(services.settings, None, manager, agent_factory(agent))
    ctx = dict(settings=services.settings, tasks=services.store, redis=services.redis, executor=executor)
    app = create_app(services.settings, services.service, services.redis)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/tasks",
                headers={"X-API-Key": "test-key"},
                json={"text": "Update VALUE", "conversation_id": "conversation-1"},
            )
            assert response.status_code == 202
            task_id = response.json()["task_id"]
            assert services.queue.items == [task_id]
            assert manager.opened == 0
            finished = await process_task(ctx, task_id)
            assert finished["status"] == "succeeded"
            status = await client.get(f"/tasks/{task_id}", headers={"X-API-Key": "test-key"})
            assert status.json()["result"]["artifacts"] == ["changes.patch", "workspace.tar.gz", "summary.json"]
            patch = await client.get(f"/tasks/{task_id}/artifacts/changes.patch", headers={"X-API-Key": "test-key"})
            assert b"+VALUE = 2" in patch.content
            assert manager.opened == manager.closed == 1
            denied = await client.get(f"/tasks/{task_id}", headers={"X-API-Key": "other-key"})
            assert denied.status_code == 404
            denied_patch = await client.get(
                f"/tasks/{task_id}/artifacts/changes.patch", headers={"X-API-Key": "other-key"}
            )
            assert denied_patch.status_code == 404
            again = await process_task(ctx, task_id)
            assert again["status"] == "succeeded" and manager.opened == 1
            next_task = await services.service.submit(
                TaskRequest(text="Continue", conversation_id="conversation-1"), "tenant-a"
            )
            await process_task(ctx, next_task.task_id)
            assert manager.restored[-1].name == "workspace.tar.gz"
            assert len(agent.calls) == 2
            assert agent.calls[0][1]["configurable"] == agent.calls[1][1]["configurable"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["model", "export", "timeout"])
async def test_failure_always_closes_sandbox(services, mode):
    manager = FakeManager()
    agent = FakeAgent(
        error=RuntimeError("model failed") if mode == "model" else None, delay=2 if mode == "timeout" else 0
    )
    if mode == "export":
        manager.export_error = RuntimeError("export failed")
    if mode == "timeout":
        services.settings.task_timeout = 0.01
    executor = TaskExecutor(services.settings, None, manager, agent_factory(agent))
    ctx = dict(settings=services.settings, tasks=services.store, redis=services.redis, executor=executor)
    record = await services.service.submit(TaskRequest(text="task"), "tenant-a")
    await process_task(ctx, record.task_id)
    failed = await services.store.get(record.task_id)
    assert failed.status == TaskStatus.failed
    assert manager.opened == manager.closed == 1
    if mode == "timeout":
        assert "timed out" in failed.error


@pytest.mark.asyncio
async def test_cancelled_execution_is_persisted_and_cleaned(services):
    manager = FakeManager()
    executor = TaskExecutor(services.settings, None, manager, agent_factory(FakeAgent(delay=30)))
    ctx = dict(settings=services.settings, tasks=services.store, redis=services.redis, executor=executor)
    record = await services.service.submit(TaskRequest(text="task"), "tenant-a")
    job = asyncio.create_task(process_task(ctx, record.task_id))
    while manager.opened == 0:
        await asyncio.sleep(0)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert manager.closed == 1
    assert (await services.store.get(record.task_id)).status == TaskStatus.failed


@pytest.mark.asyncio
async def test_same_session_does_not_run_concurrently(services):
    manager = FakeManager()
    active = peak = 0

    class Agent(FakeAgent):
        async def ainvoke(self, payload, config):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                return await super().ainvoke(payload, config)
            finally:
                active -= 1

    executor = TaskExecutor(services.settings, None, manager, agent_factory(Agent(delay=0.02)))
    ctx = dict(settings=services.settings, tasks=services.store, redis=services.redis, executor=executor)
    first = await services.service.submit(TaskRequest(text="first", conversation_id="shared"), "tenant-a")
    second = await services.service.submit(TaskRequest(text="second", conversation_id="shared"), "tenant-a")
    await asyncio.gather(process_task(ctx, first.task_id), process_task(ctx, second.task_id))
    assert peak == 1
    assert manager.opened == manager.closed == 2


@pytest.mark.asyncio
async def test_missing_prior_workspace_fails_without_resetting_conversation(services):
    first = await services.service.submit(TaskRequest(text="first", conversation_id="shared"), "tenant-a")
    await services.store.finish(first.task_id, TaskStatus.succeeded, result={"reply": "done"})
    second = await services.service.submit(TaskRequest(text="second", conversation_id="shared"), "tenant-a")
    manager = FakeManager()
    executor = TaskExecutor(services.settings, None, manager, agent_factory(FakeAgent()))
    await process_task(
        dict(settings=services.settings, tasks=services.store, redis=services.redis, executor=executor), second.task_id
    )
    assert (await services.store.get(second.task_id)).status == TaskStatus.failed
    assert manager.opened == 0


@pytest.mark.asyncio
async def test_failed_attempt_does_not_replace_successful_checkpoint(services):
    manager, agent = FakeManager(), FakeAgent()
    executor = TaskExecutor(services.settings, object(), manager, agent_factory(agent))
    ctx = dict(settings=services.settings, tasks=services.store, redis=services.redis, executor=executor)
    first = await services.service.submit(TaskRequest(text="first", conversation_id="shared"), "tenant-a")
    await process_task(ctx, first.task_id)
    successful = await services.store.get(first.task_id)
    agent.error = RuntimeError("failed intermediate task")
    second = await services.service.submit(TaskRequest(text="second", conversation_id="shared"), "tenant-a")
    await process_task(ctx, second.task_id)
    agent.error = None
    third = await services.service.submit(TaskRequest(text="third", conversation_id="shared"), "tenant-a")
    await process_task(ctx, third.task_id)
    assert agent.calls[-1][1]["configurable"]["checkpoint_id"] == successful.result["_checkpoint_id"]
    assert str(first.task_id) in str(manager.restored[-1])
    visible = task_view(await services.store.get(third.task_id))
    assert not any(key.startswith("_") for key in visible.result)


@pytest.mark.asyncio
async def test_initial_failure_starts_a_fresh_execution_thread(services):
    manager, agent = FakeManager(), FakeAgent(error=RuntimeError("failure"))
    executor = TaskExecutor(services.settings, object(), manager, agent_factory(agent))
    ctx = dict(settings=services.settings, tasks=services.store, redis=services.redis, executor=executor)
    first = await services.service.submit(TaskRequest(text="first", conversation_id="shared"), "tenant-a")
    await process_task(ctx, first.task_id)
    agent.error = None
    second = await services.service.submit(TaskRequest(text="second", conversation_id="shared"), "tenant-a")
    await process_task(ctx, second.task_id)
    assert agent.calls[0][1]["configurable"]["thread_id"] != agent.calls[1][1]["configurable"]["thread_id"]
