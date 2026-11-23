"""
作用：将框架文件工具和执行工具绑定到一个 Docker 容器。
流程：校验工作区路径 → 调用容器内命令或文件传输 → 限制时长和输出 → 返回框架协议结果。
"""

import subprocess
import tempfile
from pathlib import PurePosixPath

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox


class DockerSandbox(BaseSandbox):
    def __init__(
        self, container_id: str, workdir: str, command_timeout: int = 60, max_output_bytes: int = 32000
    ) -> None:
        self.container_id, self.workdir = container_id, workdir
        self.command_timeout, self.max_output_bytes = command_timeout, max_output_bytes

    @property
    def id(self) -> str:
        return self.container_id

    def _path(self, path: str) -> str:
        resolved = PurePosixPath(path)
        base = PurePosixPath(self.workdir)
        if not resolved.is_absolute() or ".." in resolved.parts or not resolved.is_relative_to(base):
            raise ValueError("Path is outside the sandbox project directory")
        return str(resolved)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        limit = min(timeout or self.command_timeout, self.command_timeout)
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-w",
                        self.workdir,
                        self.container_id,
                        "timeout",
                        "--signal=KILL",
                        str(limit),
                        "sh",
                        "-lc",
                        command,
                    ],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=limit + 10,
                    check=False,
                )
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
            length = output.tell()
            output.seek(max(0, length - self.max_output_bytes))
            text = output.read().decode("utf-8", errors="replace")
        return ExecuteResponse(output=text, exit_code=code, truncated=length > self.max_output_bytes)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        results = []
        for path, content in files:
            try:
                target = self._path(path)
                parent = str(PurePosixPath(target).parent)
                subprocess.run(
                    ["docker", "exec", self.id, "mkdir", "-p", parent], check=True, capture_output=True, timeout=30
                )
                process = subprocess.run(
                    ["docker", "exec", "-i", self.id, "tee", target],
                    input=content,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=30,
                )
                error = None if process.returncode == 0 else "permission_denied"
            except ValueError:
                error = "invalid_path"
            except (OSError, subprocess.SubprocessError):
                error = "permission_denied"
            results.append(FileUploadResponse(path=path, error=error))
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        results = []
        for path in paths:
            try:
                target = self._path(path)
                process = subprocess.run(["docker", "exec", self.id, "cat", target], capture_output=True, timeout=30)
                content = process.stdout if process.returncode == 0 else None
                error = None if content is not None else "file_not_found"
            except ValueError:
                content, error = None, "invalid_path"
            except (OSError, subprocess.SubprocessError):
                content, error = None, "file_not_found"
            results.append(FileDownloadResponse(path=path, content=content, error=error))
        return results
