# 作用：构建隔离任务执行环境并预装离线测试工具。
# 流程：安装 Git 与测试依赖 → 创建普通用户 → 由任务管理器启动受限容器。
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pytest==9.2.0 pytest-asyncio==1.5.0
RUN useradd -m -u 1000 agent && mkdir -p /home/agent/project && chown -R agent:agent /home/agent
ENV HOME=/home/agent
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTEST_ADDOPTS="-p no:cacheprovider"
WORKDIR /home/agent/project
USER 1000:1000
