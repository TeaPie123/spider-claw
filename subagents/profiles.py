"""
作用：集中定义规划、开发、测试及只读审查角色。
流程：按角色选模型 → 绑定工作区和职责 → 为审查者仅暴露读取工具 → 返回统一子代理配置。
"""

import asyncio

from langchain.agents import create_agent
from langchain_core.tools import StructuredTool

from infra.llm_router import build_model
from infra.settings import Settings
from middleware.runtime import RuntimeMiddleware


def build_subagents(settings: Settings, backend, semaphore: asyncio.Semaphore) -> list[dict]:
    rules = f"工作目录是 {backend.workdir}。所有文件路径必须使用该目录下的绝对路径。"
    roles = [
        ("planner", "拆解研发任务并制定验收条件。", "给出可执行计划，指出相关文件和验证方法。"),
        ("coder", "按计划完成代码修改。", "先读取现有实现，遵守项目规范，完成范围明确的修改。"),
        ("tester", "编写并运行任务所需测试。", "覆盖正常和失败情况，实际执行测试并准确报告结果。"),
    ]
    agents = [
        dict(
            name=name,
            description=description,
            system_prompt=f"你是 spider claw 的 {name}。{prompt}{rules}",
            tools=[],
            model=build_model(settings, name),
            middleware=[RuntimeMiddleware(semaphore)],
        )
        for name, description, prompt in roles
    ]

    async def read_project_file(path: str) -> str:
        response = (await backend.adownload_files([path]))[0]
        if response.error or response.content is None:
            return f"Unable to read file: {response.error}"
        return response.content[: settings.max_output_bytes].decode("utf-8", errors="replace")

    async def inspect_changes() -> str:
        response = await backend.aexecute(
            "/usr/bin/git -c core.fsmonitor=false --no-pager diff --no-ext-diff --no-textconv HEAD -- .; "
            "/usr/bin/git -c core.fsmonitor=false ls-files --others --exclude-standard"
        )
        return response.output

    tools = [
        StructuredTool.from_function(
            coroutine=read_project_file,
            name="read_project_file",
            description="读取项目工作目录内指定绝对路径的文件，用于代码审查。",
        ),
        StructuredTool.from_function(
            coroutine=inspect_changes,
            name="inspect_changes",
            description="读取当前代码差异及新增文件列表，不修改工作区。",
        ),
    ]
    reviewer = create_agent(
        model=build_model(settings, "reviewer"),
        tools=tools,
        system_prompt=f"你是 spider claw 的代码审查者。检查正确性、边界条件和安全约束，报告问题及文件位置。{rules}",
        middleware=[RuntimeMiddleware(semaphore)],
    )
    agents.append(dict(name="reviewer", description="只读审查改动并报告问题。", runnable=reviewer))
    return agents
