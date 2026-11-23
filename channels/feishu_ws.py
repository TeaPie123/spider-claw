"""
作用：将飞书长连接事件转换为统一任务 API 请求。
流程：SDK 接收事件 → 使用事件编号幂等提交 → Worker 执行并发送结果通知。
"""

import asyncio
import json

from channels.client import TaskClient
from infra.logging import get_logger
from infra.settings import get_settings


async def handle_event(body: dict) -> None:
    from channels.webhook import parse_feishu_event

    settings = get_settings()
    message = parse_feishu_event(body)
    if message is None:
        return
    key = settings.client_api_key.get_secret_value()
    if not key:
        raise ValueError("SYC_CLIENT_API_KEY is required for Feishu task submission")
    async with TaskClient(settings.api_base_url, key) as client:
        await client.submit(
            {**message, "project_id": settings.feishu_project_id, "channel": "feishu"},
            key=f"feishu:{body['header']['event_id']}",
        )


def main() -> None:
    import lark_oapi as lark

    settings = get_settings()
    from concurrent.futures import ThreadPoolExecutor

    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="spider-feishu")

    def on_message(event) -> None:
        body = json.loads(lark.JSON.marshal(event))
        future = executor.submit(asyncio.run, handle_event(body))

        def finished(result):
            try:
                result.result()
            except Exception as exc:
                get_logger().error("Feishu event failed: {}", type(exc).__name__)

        future.add_done_callback(finished)

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()
    client = lark.ws.Client(
        settings.feishu_app_id, settings.feishu_app_secret.get_secret_value(), event_handler=handler
    )
    try:
        client.start()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    main()
