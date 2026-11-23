"""
作用：验证源码快照、路径边界、容器约束及异常回收。
流程：构造受限项目和恶意归档 → 校验打包和传输 → 模拟 Docker 启动失败并检查回收。
"""

import asyncio
import io
import tarfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from sandbox.docker_manager import DockerManager, create_arguments
from sandbox.docker_sandbox import DockerSandbox
from sandbox.project import pack_project, validate_snapshot


def test_project_snapshot_excludes_credentials_and_caches(tmp_path):
    (tmp_path / "main.py").write_text("print(1)")
    (tmp_path / ".env").write_text("secret")
    (tmp_path / ".env.production").write_text("secret")
    (tmp_path / ".env.example").write_text("KEY=")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("private")
    content = pack_project(tmp_path, 10000)
    validate_snapshot(content, 10000)
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        assert set(archive.getnames()) == {"main.py", ".env.example"}


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("/absolute", "file"), ("link", "symlink")])
def test_unsafe_snapshots_are_rejected(name, kind):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        entry = tarfile.TarInfo(name)
        if kind == "symlink":
            entry.type, entry.linkname = tarfile.SYMTYPE, "/etc"
        archive.addfile(entry)
    with pytest.raises(ValueError):
        validate_snapshot(stream.getvalue(), 10000)


def test_project_size_limit(tmp_path):
    (tmp_path / "large").write_bytes(b"x" * 100)
    with pytest.raises(ValueError):
        pack_project(tmp_path, 10)


def test_file_tools_reject_outside_paths_without_docker():
    backend = DockerSandbox("test", "/home/agent/project")
    with patch("sandbox.docker_sandbox.subprocess.run") as run:
        uploaded = backend.upload_files([("/etc/passwd", b"x"), ("/home/agent/project/../escape", b"x")])
        downloaded = backend.download_files(["relative.py"])
        assert all(item.error == "invalid_path" for item in uploaded + downloaded)
        run.assert_not_called()


def test_container_arguments_keep_secrets_out(settings):
    args = create_arguments(settings, "container")
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--user") + 1] == "1000:1000"
    assert "--read-only" in args and "--cap-drop" in args
    assert "-v" not in args and "--volume" not in args and "--env" not in args
    assert settings.api_key.get_secret_value() not in args


@pytest.mark.asyncio
async def test_failed_container_creation_still_attempts_cleanup(settings):
    calls = []

    def docker(args, data=None, timeout=60):
        calls.append(args)
        if args[0] == "run":
            raise RuntimeError("creation failed")
        return b""

    with patch("sandbox.docker_manager.docker_run", side_effect=docker):
        with pytest.raises(RuntimeError, match="creation failed"):
            async with DockerManager(settings).open(Path(settings.projects["default"])):
                pytest.fail("Unexpected sandbox")
    assert calls[-1][:2] == ["rm", "-f"]


@pytest.mark.asyncio
async def test_cancellation_waits_for_creation_before_removing_container(settings):
    started, release = threading.Event(), threading.Event()
    calls = []

    def docker(args, data=None, timeout=60):
        if args[0] == "run":
            calls.append("creating")
            started.set()
            release.wait(2)
            calls.append("created")
        elif args[0] == "rm":
            calls.append("removed")
        return b""

    async def open_container():
        async with DockerManager(settings).open(Path(settings.projects["default"])):
            pytest.fail("Cancelled sandbox must not be yielded")

    with patch("sandbox.docker_manager.docker_run", side_effect=docker):
        task = asyncio.create_task(open_container())
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls == ["creating", "created", "removed"]
