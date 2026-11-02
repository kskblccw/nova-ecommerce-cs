"""健康检查与运行指标。"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from ecommerce_cs.core.metrics import render_prometheus, snapshot
from ecommerce_cs.core.settings import get_settings

router = APIRouter(prefix="/health", tags=["health"])
metrics_router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("")
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "env": settings.app_env,
    }


@metrics_router.get("")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")


@metrics_router.get("/json")
async def metrics_json() -> dict:
    return snapshot()
