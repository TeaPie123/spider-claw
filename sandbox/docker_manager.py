"""
作用：管理任务沙箱的创建、源码导入、产物导出和回收。
流程：准备快照 → 创建加固容器 → 初始化 Git 基线 → 运行任务 → 导出补丁和快照 → 强制回收。
"""

import asyncio
import shlex
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from infra.settings import Settings
from sandbox.docker_sandbox import DockerSandbox
from sandbox.project import pack_project, validate_snapshot


def docker_run(args: list[str], data: bytes | None = None, timeout: int = 60) -> bytes:
    result = subprocess.run(["docker", *args], input=data, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Docker operation failed: {result.stderr.decode('utf-8', errors='replace')[-1500:]}")
    return result.stdout


def create_arguments(settings: Settings, name: str) -> list[str]:
    return [
        "run",
        "-d",
        "--name",
        name,
        "--label",
        "spider-claw.task=true",
        "--runtime",
        settings.sandbox_runtime,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        f"{settings.sandbox_workdir}:rw,exec,size={settings.max_project_bytes * 4},uid=1000,gid=1000",
        "--tmpfs",
        "/tmp:rw,noexec,size=128m,uid=1000,gid=1000",
        "--memory",
        settings.sandbox_mem_limit,
        "--memory-swap",
        settings.sandbox_mem_limit,
        "--pids-limit",
        str(settings.sandbox_pids_limit),
        "--cpus",
        settings.sandbox_cpus,
        "--user",
        "1000:1000",
        "-w",
        settings.sandbox_workdir,
        settings.sandbox_image,
        "sleep",
        "infinity",
    ]


class DockerManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @asynccontextmanager
    async def open(self, project: Path, previous_workspace: Path | None = None):
        settings = self.settings
        if previous_workspace is not None:
            snapshot = await asyncio.to_thread(previous_workspace.read_bytes)
            validate_snapshot(snapshot, settings.max_project_bytes)
        else:
            snapshot = await asyncio.to_thread(pack_project, project, settings.max_project_bytes)
            validate_snapshot(snapshot, settings.max_project_bytes)
        name = f"spider-task-{uuid4().hex}"
        sandbox = DockerSandbox(name, settings.sandbox_workdir, settings.command_timeout, settings.max_output_bytes)
        try:
            creation = asyncio.create_task(asyncio.to_thread(docker_run, create_arguments(settings, name)))
            try:
                await asyncio.shield(creation)
            except asyncio.CancelledError:
                try:
                    await creation
                finally:
                    raise
            await asyncio.to_thread(
                docker_run, ["exec", "-i", name, "tar", "-xzf", "-", "-C", settings.sandbox_workdir], snapshot
            )
            baseline = "git init -q && git add -f . && git -c user.name=spider-claw -c user.email=local@spider.invalid commit -qm baseline --allow-empty"
            result = await sandbox.aexecute(baseline)
            if result.exit_code != 0:
                raise RuntimeError("Unable to initialize sandbox revision")
            yield sandbox
        finally:
            cleanup = asyncio.create_task(asyncio.to_thread(docker_run, ["rm", "-f", name]))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

    async def export(self, sandbox: DockerSandbox) -> tuple[bytes, bytes]:
        workdir = self.settings.sandbox_workdir
        command = (
            "git add -A -f && git diff --cached --binary HEAD > /tmp/spider-changes.patch && "
            "tar --exclude='./.git' -czf /tmp/spider-workspace.tar.gz -C " + shlex.quote(workdir) + " ."
        )
        result = await sandbox.aexecute(command)
        if result.exit_code != 0:
            raise RuntimeError("Unable to export sandbox artifacts")
        return await asyncio.to_thread(self._read_exports, sandbox.id)

    def _read_exports(self, container_id: str) -> tuple[bytes, bytes]:
        content = []
        with tempfile.TemporaryDirectory(prefix="spider-export-") as directory:
            for name in ["spider-changes.patch", "spider-workspace.tar.gz"]:
                target = Path(directory) / name
                docker_run(["cp", "-L", f"{container_id}:/tmp/{name}", str(target)])
                if target.is_symlink():
                    raise ValueError("Exported artifact must be a regular file")
                if target.stat().st_size > self.settings.max_artifact_bytes:
                    raise ValueError("Artifact exceeds configured size limit")
                content.append(target.read_bytes())
        validate_snapshot(content[1], self.settings.max_project_bytes)
        return content[0], content[1]
