"""可观测性：按配置开启 LangSmith 追踪。

LangChain/LangGraph 通过进程环境变量决定是否上报 trace，因此必须在
构建 LLM 客户端与 Graph 之前完成注入（见 main.lifespan）。
"""

import os

from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.core.settings import Settings

log = get_logger("observability")

TRACING_ENV = "LANGSMITH_TRACING"
API_KEY_ENV = "LANGSMITH_API_KEY"
PROJECT_ENV = "LANGSMITH_PROJECT"
ENDPOINT_ENV = "LANGSMITH_ENDPOINT"


def configure_tracing(settings: Settings) -> bool:
    """把配置写入环境变量，返回是否真正开启追踪。

    未配置 API Key 时主动关闭，避免 LangSmith SDK 反复重试拖慢请求。
    """
    if not settings.langsmith_tracing:
        return False

    if not settings.langsmith_api_key:
        os.environ[TRACING_ENV] = "false"
        log.warning("langsmith_tracing_skipped", reason="api_key_missing")
        return False

    os.environ[TRACING_ENV] = "true"
    os.environ[API_KEY_ENV] = settings.langsmith_api_key
    os.environ[PROJECT_ENV] = settings.langsmith_project
    if settings.langsmith_endpoint:
        os.environ[ENDPOINT_ENV] = settings.langsmith_endpoint

    log.info(
        "langsmith_tracing_enabled",
        project=settings.langsmith_project,
        endpoint=settings.langsmith_endpoint or "https://api.smith.langchain.com",
    )
    return True
