"""
作用：启动唯一的 arq 任务执行进程。
流程：设置数据库兼容事件循环 → 加载统一 Worker 配置 → 消费任务队列。
"""

import asyncio
import sys


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    from arq import run_worker

    from tasks.worker import WorkerSettings

    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
