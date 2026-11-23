"""
作用：执行一个统一任务并持久化其代码产物。
流程：解析登记项目 → 恢复成功工作区与检查点 → 限时执行 Agent → 保存产物及新检查点 → 回收沙箱。
"""

import asyncio
import hashlib
import json
from pathlib import Path

from agent.main import build_agent
from infra.settings import Settings
from sandbox.docker_manager import DockerManager
from tasks.schemas import TaskRecord


def artifact_directory(settings: Settings, tenant_id: str, task_id: str) -> Path:
    bucket = hashlib.sha256(tenant_id.encode()).hexdigest()[:24]
    root = settings.artifact_root.resolve()
    result = (root / bucket / task_id).resolve()
    if not result.is_relative_to(root):
        raise ValueError("Invalid artifact directory")
    return result


class TaskExecutor:
    def __init__(self, settings: Settings, checkpointer, manager=None, factory=None) -> None:
        self.settings, self.checkpointer = settings, checkpointer
        self.manager = manager or DockerManager(settings)
        self.factory = factory or build_agent
        self.semaphore = asyncio.Semaphore(settings.max_concurrent_llm)

    async def execute(
        self, record: TaskRecord, previous_workspace: Path | None = None, previous_state: dict | None = None
    ) -> dict:
        request = record.request
        previous_state = previous_state or {}
        execution_thread = previous_state.get("_thread_id", f"{request['thread_id']}:{record.task_id}")
        configurable = {"thread_id": execution_thread}
        if previous_state.get("_checkpoint_id"):
            configurable["checkpoint_id"] = previous_state["_checkpoint_id"]
        project = Path(self.settings.projects[request["project_id"]]).resolve(strict=True)
        async with self.manager.open(project, previous_workspace) as backend:
            agent = await self.factory(self.settings, backend, self.checkpointer, self.semaphore)
            async with asyncio.timeout(self.settings.task_timeout):
                state = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": request["text"]}]},
                    config={
                        "configurable": configurable,
                        "recursion_limit": self.settings.recursion_limit,
                    },
                )
            if state.get("__interrupt__"):
                raise RuntimeError("Task requires an unsupported interactive approval")
            checkpoint_id = None
            if self.checkpointer is not None:
                snapshot = await agent.aget_state({"configurable": {"thread_id": execution_thread}})
                checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
            messages = state.get("messages", [])
            content = messages[-1].content if messages else ""
            if isinstance(content, list):
                reply = "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
            else:
                reply = str(content)
            patch, workspace = await self.manager.export(backend)
            directory = artifact_directory(self.settings, record.tenant_id, record.task_id)
            directory.mkdir(parents=True, exist_ok=True)
            result = {"reply": reply, "artifacts": ["changes.patch", "workspace.tar.gz", "summary.json"]}
            await asyncio.to_thread((directory / "changes.patch").write_bytes, patch)
            await asyncio.to_thread((directory / "workspace.tar.gz").write_bytes, workspace)
            await asyncio.to_thread(
                (directory / "summary.json").write_text,
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return {**result, "_thread_id": execution_thread, "_checkpoint_id": checkpoint_id}
