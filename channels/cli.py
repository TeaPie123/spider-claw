"""
作用：从终端提交研发任务并查询执行结果。
流程：解析项目、会话和凭证 → 调用任务 API → 输出任务编号或等待最终结果。
"""

import argparse
import asyncio
import json
from uuid import uuid4

from channels.client import TaskClient
from infra.settings import get_settings


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    key = settings.client_api_key.get_secret_value()
    if not key:
        raise ValueError("Set SYC_CLIENT_API_KEY before using the CLI")
    async with TaskClient(args.api_url or settings.api_base_url, key) as client:
        if args.task_id:
            record = await client.get(args.task_id)
        else:
            if not args.text:
                raise ValueError("Provide a task description")
            record = await client.submit(
                {
                    "text": args.text,
                    "project_id": args.project,
                    "conversation_id": args.session or uuid4().hex,
                    "channel": "cli",
                    "user_id": args.user,
                },
                key=args.idempotency_key,
            )
        print(json.dumps(record, ensure_ascii=False, indent=2))
        if args.wait and record["status"] not in {"succeeded", "failed"}:
            record = await client.wait(record["task_id"], timeout=args.timeout)
            print(json.dumps(record, ensure_ascii=False, indent=2))
        return 1 if record["status"] == "failed" else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="spider-claw", description="spider claw task client")
    parser.add_argument("text", nargs="?")
    parser.add_argument("--project", default="default")
    parser.add_argument("--session")
    parser.add_argument("--user", default="cli")
    parser.add_argument("--api-url")
    parser.add_argument("--task-id")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    try:
        code = asyncio.run(run(args))
    except (ValueError, TimeoutError) as exc:
        parser.exit(1, f"{exc}\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
