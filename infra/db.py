"""
作用：建立 API 和 Worker 各自管理的 Postgres 连接池。
流程：读取连接配置 → 创建未打开的异步连接池 → 由进程生命周期打开和关闭。
"""

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from infra.settings import Settings


def build_pg_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        settings.database_url,
        min_size=settings.pg_pool_min,
        max_size=settings.pg_pool_max,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
        timeout=10,
    )
