"""
作用：验证唯一 Agent 工厂使用真实框架接口且默认工具可用。
流程：使用虚拟模型凭证构建 Agent → 检查编译结果 → 确认审查工具和配置契约。
"""

import asyncio

import pytest

from agent.main import build_agent
from sandbox.docker_sandbox import DockerSandbox
from subagents.profiles import build_subagents


@pytest.mark.asyncio
async def test_real_sdk_builds_with_explicit_tools_without_function_docstrings(settings):
    backend = DockerSandbox("factory-test", settings.sandbox_workdir)
    graph = await build_agent(settings, backend, None, asyncio.Semaphore(2))
    assert hasattr(graph, "ainvoke")
    agents = build_subagents(settings, backend, asyncio.Semaphore(2))
    assert [agent["name"] for agent in agents] == ["planner", "coder", "tester", "reviewer"]
    reviewer_tools = agents[-1]["runnable"].get_graph().nodes
    assert "tools" in reviewer_tools


def test_configuration_is_lazy_and_keeps_existing_env_names(monkeypatch, tmp_path):
    from infra.settings import Settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SYC_DATABASE_URL", "postgresql://test@db/existing")
    monkeypatch.setenv("SYC_SANDBOX_IMAGE", "custom-image:stable")
    settings = Settings(_env_file=None)
    assert settings.database_url.endswith("/existing")
    assert settings.sandbox_image == "custom-image:stable"
    with pytest.raises(ValueError):
        Settings(_env_file=None, sandbox_workdir="/")
