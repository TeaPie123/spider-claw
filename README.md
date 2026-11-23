# spider claw

spider claw 接收研发任务，在独立 Docker 工作区中修改代码、运行测试，并返回补丁和工作区快照。

```text
HTTP / CLI / 飞书
        ↓
统一任务 API：鉴权、项目授权、限流、幂等
        ↓
Postgres 任务记录 + Redis / arq 队列
        ↓
Worker → 唯一 Agent 工厂 → Docker 沙箱
        ↓
保存结果、补丁、工作区快照 → 查询 / 飞书通知
```

## 运行约定

- API 和渠道层提交任务，Worker 是唯一执行入口。
- 通过配置登记项目目录，调用方只提交 `project_id`；租户需要具备对应项目的访问权限。
- 登记目录用于创建初始快照，修改后的代码通过产物交付。
- 每个任务创建独立容器。相同租户、项目、渠道和会话下的任务串行执行，接续上一次成功的工作区和对话检查点。
- 失败任务的中间对话不成为后续任务的默认起点。
- 沙箱断网，项目依赖应预装进沙箱镜像。模型连接由 Worker 管理。

## 目录

| 目录或文件 | 职责 |
| --- | --- |
| `api/` | 应用生命周期和统一任务路由 |
| `tasks/service.py` | 任务提交、项目授权、幂等和结果访问 |
| `tasks/store.py` | Postgres 任务状态和会话版本 |
| `tasks/worker.py` | 执行、会话锁、漏投恢复和结果通知 |
| `agent/main.py` | 唯一 Agent 工厂 |
| `agent/dispatcher.py` | 限时执行与产物持久化 |
| `subagents/` | 规划、开发、测试和只读审查角色 |
| `sandbox/` | 源码快照、容器操作、导入导出和回收 |
| `channels/` | CLI、任务客户端和飞书适配器 |
| `gateway/` | API Key 鉴权和租户限流 |
| `infra/` | 配置、连接池、模型和日志 |
| `middleware/` | 模型并发限制和工具调用日志 |
| `obs/`、`deploy/` | 指标定义和 Prometheus 配置 |
| `scripts/` | API、Worker 启动与环境检查 |
| `tests/` | 自动化验证；订单代码位于测试夹具中 |

自有 Python 文件仅在开头说明作用与流程。工具供模型读取的描述通过注册参数维护。

## 配置

需要 Python 3.13、Postgres、Redis，以及可连接 Linux Docker daemon 的 Docker CLI。

新环境将 [.env.example](.env.example) 复制为 `.env` 后填写配置。已有 `.env` 可继续使用其中的凭证，但需要补充项目登记和租户授权。

| 配置 | 含义 |
| --- | --- |
| `SYC_API_KEY`、`SYC_MODEL_NAME`、`SYC_BASE_URL` | Worker 使用的模型服务 |
| `SYC_API_KEYS` | 客户端 API Key 到租户的 JSON 映射 |
| `SYC_CLIENT_API_KEY` | CLI 或飞书长连接调用任务 API 的凭证 |
| `SYC_PROJECTS` | 项目编号到 Worker 可读取目录的 JSON 映射 |
| `SYC_TENANT_PROJECTS` | 租户到允许访问项目编号列表的 JSON 映射 |
| `SYC_DATABASE_URL`、`SYC_REDIS_URL` | 数据库与任务队列连接 |
| `SYC_ARTIFACT_ROOT` | API 和 Worker 共同使用的产物目录 |
| `SYC_SANDBOX_IMAGE`、`SYC_SANDBOX_RUNTIME` | 沙箱镜像与已安装的容器运行时 |
| `SYC_TASK_TIMEOUT`、`SYC_SANDBOX_POOL_SIZE` | 任务时限与单 Worker 并发数 |

项目快照会过滤 `.env`、常见私钥、Git 元数据、虚拟环境和缓存。项目目录应只包含准备交给执行器处理的源码；目录内自行保存的其他敏感配置需要提前整理。

## 本地运行

安装锁定依赖：

```powershell
uv sync
```

可以使用开发编排启动本机依赖：

```powershell
docker compose -f docker-compose.prod.yml -f docker-compose.dev.yml up -d postgres redis
docker build -t spider-claw-sandbox:latest -f sandbox/Dockerfile sandbox
```

本机运行时，在 `.env` 中设置本机连接和实际项目目录，例如：

```dotenv
SYC_DATABASE_URL=postgresql://syc:change-me@localhost:15432/shanyang
SYC_REDIS_URL=redis://localhost:16379/0
SYC_API_BASE_URL=http://localhost:8000
SYC_PROJECTS={"default":"D:/your-project"}
SYC_TENANT_PROJECTS={"local":["default"]}
```

分别启动 API 与 Worker：

```powershell
uv run python -m scripts.serve
uv run python -m scripts.worker
```

提交任务并等待结果：

```powershell
uv run python -m channels.cli --project default --session work-1 --wait "为现有接口增加参数校验并运行相关测试"
```

查询已有任务：

```powershell
uv run python -m channels.cli --task-id TASK_ID
```

API 文档位于 `http://localhost:8000/docs`。环境检查使用 `uv run python -m scripts.check_environment`。

## HTTP 接口

提交与查询均携带 `X-API-Key`。提交接口接受可选的 `Idempotency-Key`，相同租户下重复提交同一请求会使用同一个任务。

```http
POST /tasks
X-API-Key: YOUR_CLIENT_KEY
Idempotency-Key: TASK_REQUEST_KEY
Content-Type: application/json

{"text":"修复项目中的异常处理并运行测试","project_id":"default","conversation_id":"work-1"}
```

提交成功返回 `202` 和 `task_id`。任务状态为 `queued`、`running`、`succeeded` 或 `failed`。

| 接口 | 用途 |
| --- | --- |
| `GET /tasks/{task_id}` | 查询状态、回复和通知异常 |
| `GET /tasks/{task_id}/artifacts/changes.patch` | 下载相对上一次成功工作区的补丁 |
| `GET /tasks/{task_id}/artifacts/workspace.tar.gz` | 下载当前完整源码快照 |
| `GET /tasks/{task_id}/artifacts/summary.json` | 下载结果摘要 |
| `POST /webhook/generic` | 使用相同请求结构提交任务 |
| `GET /healthz`、`GET /readyz` | 存活与依赖就绪检查 |
| `GET /metrics` | API 指标 |

任务已落库但队列暂时不可用时，接口返回 `503` 和已有 `task_id`。Worker 定期重投仍在等待入队的记录，可用相同幂等键重试提交。完成通知失败会独立重试，并保留 `notification_error`。

## Compose 部署

在 `.env` 中将 Postgres、Redis 主机名设为 `postgres`、`redis`，密码与 `SYC_PG_PASSWORD` 一致。将宿主机的项目目录放在 `SYC_PROJECTS_DIR/default` 下，容器中的项目登记为 `/projects/default`。

```bash
docker build -t spider-claw-sandbox:latest -f sandbox/Dockerfile sandbox
docker compose -f docker-compose.prod.yml up -d --build
```

API 端口为 `18700`，Prometheus 仅绑定本机 `19090`。API 与 Worker 共享产物卷；项目挂载为只读。Worker 使用 Docker socket 创建和回收任务容器，应部署在受信任的执行环境中。

## 飞书

安装 `uv sync --extra feishu`，配置应用凭证、`SYC_FEISHU_TENANT_ID` 和对应项目授权，并将 `SYC_FEISHU_REPLY_ENABLED` 设为 `true`。

- Webhook 使用 `/webhook/feishu`，通过 `SYC_FEISHU_VERIFICATION_TOKEN` 校验事件。当前接收普通 JSON 事件。
- 长连接使用 `uv run python -m channels.feishu_ws`，通过任务 API 入队。其 `SYC_CLIENT_API_KEY` 需要映射到配置的飞书租户。
- 两种接入方式都由 Worker 完成执行与结果通知。

Compose 可通过 `--profile feishu` 启动长连接适配器。结果通知最多尝试五次；任务状态与通知状态分别记录。

## 验证与迁移

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

普通测试使用内存持久化、队列和模拟沙箱，并通过真实 DeepAgents SDK 验证 Agent 装配。真实集成检查需要显式设置 `SPIDER_TEST_DOCKER=1` 或专用测试数据库 `SPIDER_TEST_DATABASE_URL`。

原 `/issues`、`/jobs` 和 BackgroundTasks 接口统一迁至 `/tasks`；响应从同步回复改为任务编号。数据库使用新增的 `spider_tasks` 表，已有数据库名称与 `.env` 凭证保持兼容。
