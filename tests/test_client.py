"""
作用：验证命令行与长连接渠道共用的任务客户端契约。
流程：通过 HTTP 替身提交和查询 → 检查凭证与幂等头 → 验证成功、失败和等待超时。
"""

import json

import httpx
import pytest

from channels.client import TaskClient


@pytest.mark.asyncio
async def test_client_submits_and_waits_for_terminal_state():
    calls = []

    def respond(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(202, json={"task_id": "task-1", "status": "queued"})
        return httpx.Response(200, json={"task_id": "task-1", "status": "succeeded", "result": {"reply": "done"}})

    async with TaskClient("http://api", "test-key", httpx.MockTransport(respond)) as client:
        await client.submit({"text": "task"}, "same-key")
        result = await client.wait("task-1")
    assert result["status"] == "succeeded"
    assert calls[0].headers["X-API-Key"] == "test-key"
    assert calls[0].headers["Idempotency-Key"] == "same-key"
    assert json.loads(calls[0].content) == {"text": "task"}


@pytest.mark.asyncio
async def test_client_wait_times_out_without_changing_task():
    async with TaskClient(
        "http://api",
        "test",
        httpx.MockTransport(lambda request: httpx.Response(200, json={"task_id": "task", "status": "running"})),
    ) as client:
        with pytest.raises(TimeoutError):
            await client.wait("task", timeout=0)
