"""
作用：隔离测试配置、凭证和外部追踪。
流程：创建临时项目 → 注入虚拟设置 → 为测试提供独立服务与执行上下文。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.settings import Settings
from tasks.service import TaskService
from tests.support import MemoryQueue, MemoryRedis, MemoryStore


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    return Settings(
        _env_file=None,
        api_key="test-only",
        api_keys={"test-key": "tenant-a", "other-key": "tenant-b"},
        projects={"default": str(project)},
        tenant_projects={"tenant-a": ["default"], "tenant-b": ["default"]},
        artifact_root=tmp_path / "artifacts",
        worker_metrics_port=0,
        rate_limit_per_minute=100,
        feishu_verification_token="event-token",
        feishu_tenant_id="tenant-a",
    )


@pytest.fixture
def services(settings):
    store, queue, redis = MemoryStore(), MemoryQueue(), MemoryRedis()
    return SimpleNamespace(
        store=store, queue=queue, redis=redis, service=TaskService(store, queue, settings), settings=settings
    )
