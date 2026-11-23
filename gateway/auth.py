"""
作用：将 HTTP 凭证映射为可信租户。
流程：读取 X-API-Key → 与配置进行常量时间比较 → 返回租户或拒绝请求。
"""

import secrets

from fastapi import HTTPException, Request


def require_tenant(request: Request) -> str:
    supplied = request.headers.get("X-API-Key", "")
    if supplied:
        for key, tenant in request.app.state.settings.api_keys.items():
            if secrets.compare_digest(supplied.encode(), key.encode()):
                return tenant
    raise HTTPException(401, "Invalid API key")
