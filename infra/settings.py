"""
作用：集中读取应用、项目、模型与执行配置。
流程：加载环境变量和 .env → 校验配置 → 缓存设置实例。
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYC_", env_file=".env", extra="ignore")
    api_key: SecretStr = SecretStr("")
    model_name: str = "qwen-max"
    model_provider: str = "openai"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_tiers: dict[str, str] = Field(default_factory=dict)
    max_retries: int = Field(2, ge=0)
    timeout: int = Field(60, gt=0)
    database_url: str = "postgresql://syc:syc@localhost:5432/shanyang"
    pg_pool_min: int = Field(1, ge=0)
    pg_pool_max: int = Field(10, gt=0)
    redis_url: str = "redis://localhost:6379/0"
    api_keys: dict[str, str] = Field(default_factory=dict)
    projects: dict[str, str] = Field(default_factory=dict)
    tenant_projects: dict[str, list[str]] = Field(default_factory=dict)
    artifact_root: Path = Path("artifacts")
    sandbox_provider: Literal["docker"] = "docker"
    sandbox_image: str = "spider-claw-sandbox:latest"
    sandbox_runtime: str = "runc"
    sandbox_workdir: str = "/home/agent/project"
    sandbox_mem_limit: str = "512m"
    sandbox_pids_limit: int = Field(256, gt=0)
    sandbox_cpus: str = "1.0"
    sandbox_pool_size: int = Field(4, gt=0)
    command_timeout: int = Field(60, gt=0)
    task_timeout: int = Field(900, gt=0)
    max_concurrent_llm: int = Field(8, gt=0)
    max_project_bytes: int = Field(64 * 1024 * 1024, gt=0)
    max_artifact_bytes: int = Field(16 * 1024 * 1024, gt=0)
    max_output_bytes: int = Field(32000, gt=0)
    recursion_limit: int = Field(120, gt=0)
    rate_limit_per_minute: int = Field(30, gt=0)
    api_base_url: str = "http://localhost:8000"
    client_api_key: SecretStr = SecretStr("")
    feishu_app_id: str = ""
    feishu_app_secret: SecretStr = SecretStr("")
    feishu_verification_token: SecretStr = SecretStr("")
    feishu_tenant_id: str = ""
    feishu_project_id: str = "default"
    feishu_reply_enabled: bool = False
    log_level: str = "INFO"
    worker_metrics_port: int = Field(8002, ge=0, le=65535)
    mcp_servers: dict[str, dict] = Field(default_factory=dict)
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "spider-claw"

    @field_validator("sandbox_workdir")
    @classmethod
    def validate_workdir(cls, value: str) -> str:
        from pathlib import PurePosixPath

        path = PurePosixPath(value)
        if not value.startswith("/") or ".." in path.parts or str(path) in {"/", "/tmp"}:
            raise ValueError("sandbox_workdir must be an absolute project directory")
        return str(path)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_tracing(settings: Settings) -> None:
    if settings.langsmith_tracing:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
