"""
作用：将通用 Webhook 与可信飞书事件转交统一任务服务。
流程：校验调用身份 → 规范化消息 → 幂等入队 → 立即确认接收。
"""

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request

from gateway.auth import require_tenant
from gateway.limiter import enforce_rate_limit
from tasks.schemas import TaskRequest, TaskView, task_view

router = APIRouter(prefix="/webhook", tags=["webhooks"])


def parse_feishu_event(body: dict) -> dict | None:
    if body.get("header", {}).get("event_type") != "im.message.receive_v1":
        return None
    event = body.get("event", {})
    message = event.get("message", {})
    if message.get("message_type") != "text":
        return None
    try:
        text = json.loads(message.get("content", "{}")).get("text", "").strip()
    except (ValueError, TypeError, AttributeError):
        return None
    if not text or not message.get("chat_id"):
        return None
    return {
        "text": text,
        "conversation_id": message["chat_id"],
        "user_id": event.get("sender", {}).get("sender_id", {}).get("open_id", "unknown"),
    }


@router.post("/generic", response_model=TaskView, status_code=202)
async def generic_webhook(payload: TaskRequest, request: Request, tenant: str = Depends(require_tenant)) -> TaskView:
    await enforce_rate_limit(request.app.state.redis, tenant, request.app.state.settings.rate_limit_per_minute)
    record = await request.app.state.tasks.submit(payload, tenant, request.headers.get("Idempotency-Key"))
    return task_view(record)


@router.post("/feishu")
async def feishu_webhook(request: Request):
    body = await request.json()
    settings = request.app.state.settings
    token = body.get("token") or body.get("header", {}).get("token", "")
    expected = settings.feishu_verification_token.get_secret_value()
    if not expected or not secrets.compare_digest(str(token).encode(), expected.encode()):
        raise HTTPException(401, "Invalid Feishu verification token")
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}
    inbound = parse_feishu_event(body)
    if inbound is None:
        return {"code": 0}
    event_id = body.get("header", {}).get("event_id")
    if not event_id or not settings.feishu_tenant_id:
        raise HTTPException(400, "Feishu event or tenant configuration is missing")
    await enforce_rate_limit(request.app.state.redis, settings.feishu_tenant_id, settings.rate_limit_per_minute)
    task = TaskRequest(**inbound, channel="feishu", project_id=settings.feishu_project_id)
    record = await request.app.state.tasks.submit(
        task,
        settings.feishu_tenant_id,
        f"feishu:{event_id}",
        reply_to=inbound["conversation_id"],
    )
    return {"code": 0, "task_id": record.task_id}
