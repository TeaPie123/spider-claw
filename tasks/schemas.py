"""
作用：定义所有渠道共用的任务请求、状态和执行结果。
流程：规范化请求 → 绑定租户与任务编号 → 在 API、队列和 Worker 间传递。
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=50000)
    project_id: str = Field("default", min_length=1, max_length=100)
    conversation_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=200)
    user_id: str = Field("anonymous", min_length=1, max_length=200)
    channel: str = Field("api", min_length=1, max_length=30)


class TaskRecord(BaseModel):
    task_id: str
    tenant_id: str
    status: TaskStatus
    request: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    notification_error: str | None = None
    notified: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskView(BaseModel):
    task_id: str
    status: TaskStatus
    project_id: str
    conversation_id: str
    result: dict[str, Any] | None = None
    error: str | None = None
    notification_error: str | None = None


def task_view(record: TaskRecord) -> TaskView:
    result = {key: value for key, value in record.result.items() if not key.startswith("_")} if record.result else None
    return TaskView(
        task_id=record.task_id,
        status=record.status,
        project_id=record.request["project_id"],
        conversation_id=record.request["conversation_id"],
        result=result,
        error=record.error,
        notification_error=record.notification_error,
    )
