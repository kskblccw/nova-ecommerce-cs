"""健康检查与指标端点测试（直接调用路由函数，避免启动完整 lifespan）。"""

import pytest

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
