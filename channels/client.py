"""
作用：为 CLI、飞书长连接和验证脚本提供同一个任务 API 客户端。
流程：携带凭证提交任务 → 轮询状态 → 返回结果或超时。
"""

import asyncio
import time
from typing import Any

import httpx


class TaskClient:
    def __init__(self, base_url: str, api_key: str, transport=None) -> None:
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers={"X-API-Key": api_key}, timeout=30, transport=transport
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def submit(self, payload: dict[str, Any], key: str | None = None) -> dict:
        headers = {"Idempotency-Key": key} if key else {}
        response = await self.client.post("/tasks", json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    async def get(self, task_id: str) -> dict:
        response = await self.client.get(f"/tasks/{task_id}")
        response.raise_for_status()
        return response.json()

    async def wait(self, task_id: str, timeout: float = 900, interval: float = 1) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            record = await self.get(task_id)
            if record["status"] in {"succeeded", "failed"}:
                return record
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Task {task_id} is still running")
            await asyncio.sleep(min(interval, max(0, deadline - time.monotonic())))
