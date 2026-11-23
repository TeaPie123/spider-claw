# 作用：构建 API、Worker 与飞书适配器共用的应用镜像。
# 流程：提供 Docker CLI → 安装锁定依赖 → 复制源码 → 启动任务 API。
FROM docker:27-cli AS docker_cli
FROM python:3.13-slim
WORKDIR /app
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "scripts.serve", "--host", "0.0.0.0", "--port", "8000"]
