"""
作用：限制当前 Worker 的模型调用并发并记录工具调用结果。
流程：模型调用前获取并发名额 → 调用模型 → 释放名额；工具调用记录名称与成败。
"""

import asyncio

from langchain.agents.middleware import AgentMiddleware

from infra.logging import get_logger


class RuntimeMiddleware(AgentMiddleware):
    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self.semaphore = semaphore

    async def awrap_model_call(self, request, handler):
        async with self.semaphore:
            return await handler(request)

    async def awrap_tool_call(self, request, handler):
        name = request.tool_call.get("name", "unknown")
        try:
            result = await handler(request)
        except Exception:
            get_logger().warning("Tool failed: {}", name)
            raise
        get_logger().info("Tool completed: {}", name)
        return result
