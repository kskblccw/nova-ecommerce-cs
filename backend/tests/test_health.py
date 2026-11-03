"""健康检查与指标端点测试（直接调用路由函数，避免启动完整 lifespan）。"""

import json
from types import SimpleNamespace

import pytest

from ecommerce_cs.api.routers import health as health_module
from ecommerce_cs.api.routers.health import health, metrics, metrics_json


@pytest.mark.asyncio
async def test_health_reports_ok():
    payload = await health()

    assert payload["status"] == "ok"
    assert payload["service"]
    assert payload["env"]


@pytest.mark.asyncio
async def test_metrics_json_returns_counter_snapshot():
    payload = await metrics_json()
    assert isinstance(payload, dict)


@pytest.mark.asyncio
async def test_metrics_prometheus_payload_has_type_header():
    response = await metrics()

    body = response.body.decode("utf-8")
    assert "# TYPE cs_counter_total counter" in body
    assert response.media_type == "text/plain; version=0.0.4"


def _patch_checks(monkeypatch, *, database: bool, redis: bool, checkpointer: bool) -> None:
    async def fake_database():
        return {"ok": database, "latency_ms": 1.5}

    async def fake_redis():
        return {"ok": redis, "mode": "redis" if redis else "memory"}

    monkeypatch.setattr(health_module, "_check_database", fake_database)
    monkeypatch.setattr(health_module, "_check_redis", fake_redis)
    monkeypatch.setattr(
        health_module,
        "_check_checkpointer",
        lambda: {"ok": checkpointer, "backend": "postgres"},
    )


@pytest.mark.asyncio
async def test_ready_reports_ready_when_all_checks_pass(monkeypatch):
    _patch_checks(monkeypatch, database=True, redis=True, checkpointer=True)

    response = await health_module.ready()
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["status"] == "ready"
    assert payload["checks"]["checkpointer"]["backend"] == "postgres"


@pytest.mark.asyncio
async def test_ready_returns_503_when_database_down(monkeypatch):
    _patch_checks(monkeypatch, database=False, redis=True, checkpointer=True)

    response = await health_module.ready()
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["status"] == "unavailable"
    assert payload["checks"]["database"]["ok"] is False


@pytest.mark.asyncio
async def test_ready_returns_503_without_checkpointer(monkeypatch):
    _patch_checks(monkeypatch, database=True, redis=True, checkpointer=False)

    response = await health_module.ready()

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_ready_marks_degraded_when_redis_down(monkeypatch):
    _patch_checks(monkeypatch, database=True, redis=False, checkpointer=True)

    response = await health_module.ready()
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["status"] == "degraded"
    assert payload["checks"]["redis"]["mode"] == "memory"


@pytest.mark.asyncio
async def test_check_redis_reports_memory_when_unconfigured(monkeypatch):
    monkeypatch.setattr(
        health_module,
        "get_settings",
        lambda: SimpleNamespace(redis_url="", checkpointer_backend="memory"),
    )

    payload = await health_module._check_redis()

    assert payload == {
        "ok": True,
        "mode": "memory",
        "detail": "未配置 REDIS_URL，使用进程内降级实现",
    }


def test_check_checkpointer_returns_false_without_graph(monkeypatch):
    monkeypatch.setattr(health_module, "get_checkpointer", lambda: None)
    monkeypatch.setattr(
        health_module,
        "get_settings",
        lambda: SimpleNamespace(checkpointer_backend="postgres"),
    )

    assert health_module._check_checkpointer() == {"ok": False, "backend": "postgres"}


@pytest.mark.asyncio
async def test_check_database_reports_failure(monkeypatch):
    class _BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr(health_module, "SessionLocal", lambda: _BrokenSession())

    payload = await health_module._check_database()

    assert payload["ok"] is False
    assert payload["latency_ms"] >= 0
