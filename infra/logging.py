"""
作用：提供不打印凭证和局部变量的统一日志。
流程：读取日志级别 → 配置控制台输出 → 返回全局日志对象。
"""

import sys

from loguru import logger

from infra.settings import get_settings

_configured = False


def get_logger():
    global _configured
    if not _configured:
        logger.remove()
        logger.add(sys.stderr, level=get_settings().log_level, diagnose=False, backtrace=False)
        _configured = True
    return logger
