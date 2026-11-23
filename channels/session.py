"""
作用：为项目会话生成稳定且隔离的执行线程编号。
流程：组合租户、项目、渠道和会话 → 序列化 → 计算摘要。
"""

import hashlib
import json


def thread_id_for(tenant_id: str, project_id: str, channel: str, conversation_id: str) -> str:
    raw = json.dumps([tenant_id, project_id, channel, conversation_id], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
