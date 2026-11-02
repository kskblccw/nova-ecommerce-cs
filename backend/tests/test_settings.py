"""生产配置校验测试。"""

import pytest

from ecommerce_cs.core.settings import Settings, validate_settings


def test_production_rejects_default_secrets():
    settings = Settings(_env_file=None, app_env="prod")
    with pytest.raises(RuntimeError) as exc:
        validate_settings(settings)
    assert "JWT_SECRET" in str(exc.value)
    assert "COOKIE_SECURE" in str(exc.value)


def test_production_accepts_hardened_settings():
    settings = Settings(
        _env_file=None,
        app_env="prod",
        jwt_secret="a" * 40,
        cookie_secure=True,
        database_url="postgresql+asyncpg://app:secret@db:5432/ecommerce_cs",
        dashscope_api_key="sk-test",
        redis_url="redis://redis:6379/0",
    )
    validate_settings(settings)


def test_dev_does_not_validate_production_rules():
    validate_settings(Settings(_env_file=None, app_env="dev"))


def test_database_url_helpers_use_psycopg3():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:pass@localhost:5432/demo",
    )
    assert settings.sync_database_url == "postgresql+psycopg://user:pass@localhost:5432/demo"
    assert settings.psycopg_conninfo == "postgresql://user:pass@localhost:5432/demo"


def test_forwarded_for_is_not_trusted_by_default():
    assert Settings(_env_file=None).trust_forwarded_for is False
