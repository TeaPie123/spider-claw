"""
作用：封装唯一的 arq 队列及任务入队操作。
流程：创建 Redis 连接池 → 以任务编号入队 → Worker 从数据库读取完整请求。
"""

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from infra.settings import Settings


async def open_queue(settings: Settings) -> ArqRedis:
    return await create_pool(RedisSettings.from_dsn(settings.redis_url))


class TaskQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self.redis = redis

    async def enqueue(self, task_id: str) -> None:
        await self.redis.enqueue_job("process_task", task_id, _job_id=task_id)
