"""全局配置：唯一入口，禁止在业务模块里散落常量。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[3]

DEFAULT_JWT_SECRETS = {
    "dev-only-secret-change-me",
    "change-me-to-a-long-random-string",
    "change-me-in-production",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "dev"
    app_name: str = "电商智能客服"
    api_prefix: str = "/api"
    log_level: str = "INFO"
    debug: bool = False

    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/ecommerce_cs"
    )
    checkpointer_backend: str = "postgres"

    jwt_secret: str = "dev-only-secret-change-me"
    jwt_expire_minutes: int = 10080
    cookie_name: str = "cs_token"
    cookie_secure: bool = False

    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v3"
    embedding_dim: int = 1024

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    history_window: int = 12
    max_agent_steps: int = 6
    human_hotline: str = "400-888-8888"
    admin_phone: str = "13900000000"

    # ── 可观测性（LangSmith 追踪）─────────────────────────────────────────────
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "nova-cs"
    langsmith_endpoint: str = ""

    # ── 限流与防刷 ────────────────────────────────────────────────────────────
    rate_limit_enabled: bool = True
    rate_limit_login_per_minute: int = 10
    rate_limit_register_per_hour: int = 10
    rate_limit_chat_per_minute: int = 20
    rate_limit_chat_per_day: int = 500
    max_concurrent_streams_per_user: int = 1
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    login_max_ip_failures: int = 20
    login_ip_lock_minutes: int = 15
    redis_url: str = ""
    rate_limit_key_prefix: str = "cs:rl"
    rate_limit_redis_timeout_seconds: float = 1.0
    trust_forwarded_for: bool = False  # 仅当部署在可信反向代理后时开启

    # ── RAG 双路召回 ──────────────────────────────────────────────────────────
    rag_vector_backend: str = "auto"  # auto / milvus / pgvector / numpy
    # 不能叫 MILVUS_URI：pymilvus 会把它当成服务地址，本地文件路径会导致导入失败
    milvus_db_uri: str = str(BASE_DIR / "data" / "milvus.db")  # 本地文件=Milvus Lite
    milvus_collection: str = "rag_chunks"
    rag_vector_candidates: int = 12
    rag_keyword_candidates: int = 12
    rag_rrf_k: int = 10
    rag_vector_weight: float = 2.0
    rag_keyword_weight: float = 1.0
    rag_keyword_max_terms: int = 8

    # ── Agent 运行护栏与降级 ──────────────────────────────────────────────────
    agent_recursion_limit: int = 0  # 0 表示按 max_agent_steps 自动推导
    agent_timeout_seconds: float = 120.0
    max_tool_calls: int = 8
    tool_timeout_seconds: float = 30.0
    llm_timeout_seconds: float = 45.0
    llm_max_retries: int = 2
    llm_max_concurrency: int = 8
    llm_failure_threshold: int = 5
    llm_cooldown_seconds: int = 60

    # ── 数据保留与清理 ────────────────────────────────────────────────────────
    pending_action_ttl_minutes: int = 30
    auth_session_retention_days: int = 30
    retrieval_log_retention_days: int = 30
    checkpoint_retention_days: int = 30
    cleanup_interval_minutes: int = 60
    cleanup_on_startup: bool = True

    # ── 记忆压缩 ──────────────────────────────────────────────────────────────
    memory_min_interval_minutes: int = 5

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def sync_database_url(self) -> str:
        """SQLAlchemy / Alembic 使用的同步 URL，统一走 psycopg3。"""
        url = self.database_url
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "+psycopg", 1)
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

    @property
    def psycopg_conninfo(self) -> str:
        """psycopg 连接池使用的 libpq conninfo（不能带 SQLAlchemy 驱动后缀）。"""
        return self.database_url.replace("+asyncpg", "").replace("+psycopg", "")

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in ("prod", "production")


def validate_settings(settings: Settings) -> None:
    """生产环境配置 fail-fast，避免默认密钥/默认口令带病上线。"""
    if not settings.is_production:
        return

    problems: list[str] = []
    if settings.jwt_secret in DEFAULT_JWT_SECRETS or len(settings.jwt_secret) < 32:
        problems.append("JWT_SECRET 必须替换为至少 32 位的随机字符串")
    if not settings.cookie_secure:
        problems.append("生产环境必须设置 COOKIE_SECURE=true")
    if "postgres:postgres@" in settings.database_url:
        problems.append("DATABASE_URL 不能使用默认 postgres/postgres 口令")
    if not settings.dashscope_api_key:
        problems.append("DASHSCOPE_API_KEY 未配置")
    if not settings.redis_url:
        problems.append("REDIS_URL 未配置，多实例限流无法生效")
    if problems:
        raise RuntimeError("生产配置校验失败：\n- " + "\n- ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
