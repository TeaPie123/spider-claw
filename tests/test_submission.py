"""
作用：验证提交权限、幂等、失败状态和渠道一致性。
流程：构造正常与异常请求 → 调用统一服务或 API → 检查持久化与投递结果。
"""

import httpx
import pytest
from pydantic import ValidationError

from api.app import create_app
from tasks.schemas import TaskRequest, TaskStatus
from tasks.service import AccessDenied, QueueUnavailable


@pytest.mark.asyncio
async def test_idempotent_retry_without_explicit_conversation(services):
    first = await services.service.submit(TaskRequest(text="task"), "tenant-a", "unique-key")
    again = await services.service.submit(TaskRequest(text="task"), "tenant-a", "unique-key")
    assert first.task_id == again.task_id
    assert len(services.queue.items) == 1
    with pytest.raises(ValueError):
        await services.service.submit(TaskRequest(text="different"), "tenant-a", "unique-key")


@pytest.mark.asyncio
async def test_idempotency_is_scoped_to_tenant(services):
    a = await services.service.submit(TaskRequest(text="task"), "tenant-a", "same-key")
    b = await services.service.submit(TaskRequest(text="task"), "tenant-b", "same-key")
    assert a.task_id != b.task_id
    assert a.request["thread_id"] != b.request["thread_id"]


@pytest.mark.asyncio
async def test_unauthorized_project_is_not_persisted(services):
    with pytest.raises(AccessDenied):
        await services.service.submit(TaskRequest(text="task", project_id="/private/path"), "tenant-a")
    assert not services.store.records and not services.queue.items


@pytest.mark.asyncio
async def test_queue_outage_keeps_record_available_for_recovery(services):
    services.queue.fail = True
    with pytest.raises(QueueUnavailable):
        await services.service.submit(TaskRequest(text="task"), "tenant-a")
    record = next(iter(services.store.records.values()))
    assert record.status == TaskStatus.queued
    assert record.error == "Waiting for queue recovery"


@pytest.mark.asyncio
async def test_api_requires_auth_and_validates_request(services):
    app = create_app(services.settings, services.service, services.redis)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path in ["/tasks", "/webhook/generic"]:
                assert (await client.post(path, json={"text": "task"})).status_code == 401
                invalid = await client.post(path, json={"text": "   "}, headers={"X-API-Key": "test-key"})
                assert invalid.status_code == 422
                valid = await client.post(
                    path,
                    json={"text": "task", "conversation_id": "same"},
                    headers={"X-API-Key": "test-key", "Idempotency-Key": "shared"},
                )
                assert valid.status_code == 202
            assert len(services.queue.items) == 1


@pytest.mark.asyncio
async def test_submission_limit_is_shared_by_entrypoints(services):
    services.settings.rate_limit_per_minute = 1
    app = create_app(services.settings, services.service, services.redis)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (
                await client.post("/tasks", json={"text": "one"}, headers={"X-API-Key": "test-key"})
            ).status_code == 202
            assert (
                await client.post("/webhook/generic", json={"text": "two"}, headers={"X-API-Key": "test-key"})
            ).status_code == 429


@pytest.mark.asyncio
async def test_public_payload_cannot_choose_reply_destination(services):
    with pytest.raises(ValidationError):
        TaskRequest(text="task", channel="feishu", reply_to="untrusted-chat")


@pytest.mark.asyncio
async def test_feishu_event_uses_same_queue_and_deduplicates(services):
    import json

    app = create_app(services.settings, services.service, services.redis)
    body = {
        "header": {"event_id": "event-1", "event_type": "im.message.receive_v1", "token": "event-token"},
        "event": {
            "message": {"message_type": "text", "chat_id": "chat-1", "content": json.dumps({"text": "task"})},
            "sender": {"sender_id": {"open_id": "user-1"}},
        },
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/webhook/feishu", json=body)
            second = await client.post("/webhook/feishu", json=body)
            assert first.json()["task_id"] == second.json()["task_id"]
            assert len(services.queue.items) == 1
            record = next(iter(services.store.records.values()))
            assert record.request["reply_to"] == "chat-1"
            body["header"]["token"] = "wrong"
            assert (await client.post("/webhook/feishu", json=body)).status_code == 401
