"""
作用：检查运行主流程所需的配置与 Docker 环境。
流程：验证项目登记和租户授权 → 检查 Docker CLI、daemon 和沙箱镜像 → 输出检查结果。
"""

import shutil
import subprocess
from pathlib import Path

from infra.settings import get_settings


def main() -> None:
    settings = get_settings()
    errors = []
    if not settings.api_keys:
        errors.append("SYC_API_KEYS is empty")
    if not settings.api_key.get_secret_value():
        errors.append("SYC_API_KEY is empty")
    if not settings.projects:
        errors.append("SYC_PROJECTS is empty")
    for project_id, location in settings.projects.items():
        if not Path(location).is_dir():
            errors.append(f"Project {project_id} is not available in this process")
    if not shutil.which("docker"):
        errors.append("Docker CLI was not found")
    else:
        for args in [
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            ["docker", "image", "inspect", settings.sandbox_image],
        ]:
            try:
                subprocess.run(args, check=True, capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                errors.append(f"Docker check failed: {' '.join(args[:3])}")
    for error in errors:
        print(error)
    if errors:
        raise SystemExit(1)
    print("spider claw configuration and Docker checks passed")


if __name__ == "__main__":
    main()
