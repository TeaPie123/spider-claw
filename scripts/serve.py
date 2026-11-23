"""
作用：启动统一任务 API 并设置平台所需事件循环。
流程：选择 Windows 兼容策略 → 读取监听参数 → 启动 Uvicorn。
"""

import argparse
import asyncio
import sys

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="spider claw API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    uvicorn.run("api.app:app", host=args.host, port=args.port, loop="asyncio")


if __name__ == "__main__":
    main()
