"""
作用：在 Redis 中共享租户提交频率限制。
流程：按租户和时间窗口计数 → 原子设置过期 → 超限返回 429。
"""

import hashlib
import time

from fastapi import HTTPException

RATE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], 120) end
return count
"""


async def enforce_rate_limit(redis, tenant_id: str, limit: int) -> None:
    bucket = hashlib.sha256(tenant_id.encode()).hexdigest()
    count = await redis.eval(RATE_SCRIPT, 1, f"spider:rate:{bucket}:{int(time.time()) // 60}")
    if count > limit:
        raise HTTPException(429, "Submission rate exceeded", headers={"Retry-After": "60"})
