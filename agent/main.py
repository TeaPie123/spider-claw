"""
作用：提供唯一的 spider claw Agent 工厂。
流程：装配模型、沙箱、项目规范、工具和子代理 → 注入持久化检查点 → 返回可执行 Agent。
"""

import asyncio

from deepagents import create_deep_agent

from infra.llm_router import build_model
from infra.settings import Settings
from middleware.runtime import RuntimeMiddleware
from subagents.profiles import build_subagents
from tools.registry import load_tools

SYSTEM_PROMPT = """你是 spider claw，一个在隔离工作区中完成研发任务的助手。
先阅读项目与 AGENTS.md，制定计划，再修改代码并运行必要的测试。
所有命令和文件操作都在提供的沙箱内执行，使用工作目录下的绝对路径。
复杂任务可委派给 planner、coder、tester 和 reviewer；委派时说明上下文与验收条件。
如实报告测试结果和未完成事项，不声称运行过未执行的命令。
最终说明改动、验证结果及仍需处理的问题。平台将自动保存补丁与工作区快照。
"""


async def build_agent(settings: Settings, backend, checkpointer, semaphore: asyncio.Semaphore):
    return create_deep_agent(
        name="spider-claw",
        model=build_model(settings),
        backend=backend,
        system_prompt=SYSTEM_PROMPT + f"\n当前工作目录：{backend.workdir}",
        tools=await load_tools(settings),
        subagents=build_subagents(settings, backend, semaphore),
        middleware=[RuntimeMiddleware(semaphore)],
        checkpointer=checkpointer,
        memory=[f"{backend.workdir}/AGENTS.md"],
        skills=[f"{backend.workdir}/skills/"],
    )
