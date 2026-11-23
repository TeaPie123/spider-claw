"""
作用：发送统一任务服务生成的飞书结果通知。
流程：构造飞书文本消息 → 在线程中调用 SDK → 检查接口结果。
"""

import asyncio
import json

from infra.settings import Settings


class FeishuChannel:
    def __init__(self, settings: Settings) -> None:
        import lark_oapi as lark

        self.client = (
            lark.Client.builder()
            .app_id(settings.feishu_app_id)
            .app_secret(settings.feishu_app_secret.get_secret_value())
            .build()
        )

    async def send(self, conversation_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(conversation_id)
            .msg_type("text")
            .content(json.dumps({"text": text[:20000]}, ensure_ascii=False))
            .build()
        )
        request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        response = await asyncio.to_thread(self.client.im.v1.message.create, request)
        if not response.success():
            raise RuntimeError(f"Feishu delivery failed with code {response.code}")
