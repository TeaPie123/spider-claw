"""
作用：依据角色配置构建模型连接。
流程：选取角色对应模型档位 → 校验凭证 → 创建带超时和重试限制的模型。
"""

from langchain.chat_models import init_chat_model

from infra.settings import Settings

ROLE_TIERS = {"main": "strong", "planner": "strong", "coder": "strong", "tester": "standard", "reviewer": "strong"}


def build_model(settings: Settings, role: str = "main"):
    if not settings.api_key.get_secret_value():
        raise ValueError("Model API key is not configured")
    model_name = settings.model_tiers.get(ROLE_TIERS.get(role, "strong"), settings.model_name)
    options = dict(
        model=model_name,
        model_provider=settings.model_provider,
        api_key=settings.api_key.get_secret_value(),
        base_url=settings.base_url,
        temperature=0,
        max_retries=settings.max_retries,
        timeout=settings.timeout,
    )
    if settings.model_provider == "openai":
        options["use_responses_api"] = False
    return init_chat_model(**options)
