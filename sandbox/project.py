"""
作用：收集项目源码并构建可安全传入沙箱的快照。
流程：遍历登记目录 → 排除凭证、缓存和符号链接 → 检查体积 → 打包文件。
"""

import fnmatch
import io
import os
import tarfile
from pathlib import Path, PurePosixPath

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".aws",
    ".ssh",
    ".codex",
    ".idea",
    "logs",
    "artifacts",
    "dist",
    "build",
}
EXCLUDED_FILES = {".env", ".netrc", "credentials", "credentials.json", "id_rsa", "id_ed25519"}


def excluded(path: Path) -> bool:
    return (
        any(part in EXCLUDED_DIRS for part in path.parts)
        or path.name in EXCLUDED_FILES
        or (path.name.startswith(".env.") and path.name not in {".env.example", ".env.sample"})
        or any(fnmatch.fnmatch(path.name, pattern) for pattern in ("*.pem", "*.key", "*.p12"))
    )


def pack_project(root: Path, max_bytes: int) -> bytes:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Registered project is not a directory")
    stream = io.BytesIO()
    total = count = 0
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for parent, directories, files in os.walk(root, followlinks=False):
            directories[:] = sorted(
                name
                for name in directories
                if not excluded((Path(parent) / name).relative_to(root)) and not (Path(parent) / name).is_symlink()
            )
            for name in sorted(files):
                path = Path(parent) / name
                relative = path.relative_to(root)
                if excluded(relative) or path.is_symlink() or not path.is_file():
                    continue
                total += path.stat().st_size
                count += 1
                if total > max_bytes or count > 20000:
                    raise ValueError("Project snapshot exceeds configured limits")
                archive.add(path, arcname=relative.as_posix(), recursive=False)
    return stream.getvalue()


def validate_snapshot(content: bytes, max_bytes: int) -> None:
    total = count = 0
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            count += 1
            total += member.size
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise ValueError("Unsafe snapshot entry")
            if not member.isfile() and not member.isdir():
                raise ValueError("Unsupported snapshot entry")
            if total > max_bytes or count > 20000:
                raise ValueError("Workspace snapshot exceeds configured limits")
