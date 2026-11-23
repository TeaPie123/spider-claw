"""
作用：向所有 HTTP 客户端提供统一任务接口。
流程：验证租户和限流 → 调用任务服务 → 返回状态或经过授权的执行产物。
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse

from gateway.auth import require_tenant
from gateway.limiter import enforce_rate_limit
from tasks.schemas import TaskRequest, TaskView, task_view

router = APIRouter(tags=["tasks"])


@router.post("/tasks", response_model=TaskView, status_code=202)
async def submit_task(
    payload: TaskRequest,
    request: Request,
    tenant: str = Depends(require_tenant),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> TaskView:
    await enforce_rate_limit(request.app.state.redis, tenant, request.app.state.settings.rate_limit_per_minute)
    reply_to = None
    if payload.channel == "feishu":
        if tenant != request.app.state.settings.feishu_tenant_id:
            raise HTTPException(403, "Feishu adapter tenant is not authorized")
        reply_to = payload.conversation_id
    record = await request.app.state.tasks.submit(payload, tenant, idempotency_key, reply_to=reply_to)
    return task_view(record)


@router.get("/tasks/{task_id}", response_model=TaskView)
async def get_task(task_id: str, request: Request, tenant: str = Depends(require_tenant)) -> TaskView:
    return task_view(await request.app.state.tasks.get(task_id, tenant))


@router.get("/tasks/{task_id}/artifacts/{name}")
async def get_artifact(
    task_id: str, name: str, request: Request, tenant: str = Depends(require_tenant)
) -> FileResponse:
    path = await request.app.state.tasks.artifact(task_id, tenant, name)
    return FileResponse(path, filename=name, media_type="application/octet-stream")
