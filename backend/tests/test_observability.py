"""LangSmith 追踪配置注入测试。"""

import os
from types import SimpleNamespace

import pytest

from ecommerce_cs.core.observability import configure_tracing
from ecommerce_cs.core.settings import Settings

ENV_KEYS = (
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
)


@pytest.fixture
def clean_tracing_env():
    """隔离外部环境变量，测试结束后还原，避免污染其他用例。"""
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_tracing_is_off_by_default(clean_tracing_env):
    settings = _settings()
    assert settings.langsmith_tracing is False
    assert settings.langsmith_project == "nova-cs"
    assert configure_tracing(settings) is False
    assert os.environ.get("LANGSMITH_TRACING") != "true"


def test_tracing_injects_environment(clean_tracing_env):
    settings = _settings(
        langsmith_tracing=True,
        langsmith_api_key="lsv2-test-key",
        langsmith_project="nova-test",
        langsmith_endpoint="https://eu.api.smith.langchain.com",
    )

    assert configure_tracing(settings) is True
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_API_KEY"] == "lsv2-test-key"
    assert os.environ["LANGSMITH_PROJECT"] == "nova-test"
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://eu.api.smith.langchain.com"


def test_tracing_without_api_key_is_disabled(clean_tracing_env, monkeypatch):
    warnings: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "ecommerce_cs.core.observability.log",
        SimpleNamespace(warning=lambda event, **kwargs: warnings.append((event, kwargs))),
    )

    settings = _settings(langsmith_tracing=True, langsmith_api_key="")

    assert configure_tracing(settings) is False
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert warnings and warnings[0][0] == "langsmith_tracing_skipped"


def test_settings_reads_tracing_from_env(clean_tracing_env, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-env-key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "env-project")

    settings = _settings()

    assert settings.langsmith_tracing is True
    assert settings.langsmith_api_key == "lsv2-env-key"
    assert settings.langsmith_project == "env-project"
    assert configure_tracing(settings) is True
