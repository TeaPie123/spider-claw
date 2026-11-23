"""
作用：装配显式配置的扩展工具。
流程：读取 MCP 配置 → 加载可选连接器 → 取得工具；未配置时返回空列表。
"""

from infra.settings import Settings


async def load_tools(settings: Settings) -> list:
    if not settings.mcp_servers:
        return []
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:
        raise RuntimeError("Install the mcp extra to use configured MCP servers") from exc
    client = MultiServerMCPClient(settings.mcp_servers)
    return await client.get_tools()
