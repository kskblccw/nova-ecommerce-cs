"""健康检查与运行指标。"""

import asyncio
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.core.metrics import render_prometheus, snapshot
from ecommerce_cs.core.ratelimit import service as rate_limit_service
from ecommerce_cs.core.settings import get_settings
from ecommerce_cs.db.session import SessionLocal
from ecommerce_cs.runtime import get_checkpointer

router = APIRouter(prefix="/health", tags=["health"])
metrics_router = APIRouter(prefix="/metrics", tags=["metrics"])

log = get_logger("health")

_DB_CHECK_TIMEOUT_SECONDS = 3.0


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


@router.get("")
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "env": settings.app_env,
    }


async def _check_database() -> dict:
    started = time.perf_counter()
    try:
        async with SessionLocal() as session:
            await asyncio.wait_for(
                session.execute(text("SELECT 1")), timeout=_DB_CHECK_TIMEOUT_SECONDS
            )
    except Exception as exc:  # 探针需要吞掉所有异常并上报
        log.warning("health_database_unavailable", error=str(exc))
        return {"ok": False, "latency_ms": _elapsed_ms(started)}
    return {"ok": True, "latency_ms": _elapsed_ms(started)}


async def _check_redis() -> dict:
    settings = get_settings()
    if not settings.redis_url:
        return {
            "ok": True,
            "mode": "memory",
            "detail": "未配置 REDIS_URL，使用进程内降级实现",
        }
    started = time.perf_counter()
    healthy = await rate_limit_service.ping()
    return {
        "ok": healthy,
        "mode": "redis" if healthy else "memory",
        "detail": "" if healthy else "Redis 不可用，已降级为进程内限流",
        "latency_ms": _elapsed_ms(started),
    }


def _check_checkpointer() -> dict:
    settings = get_settings()
    return {
        "ok": get_checkpointer() is not None,
        "backend": settings.checkpointer_backend,
    }


@router.get("/ready")
async def ready() -> JSONResponse:
    """就绪探针：数据库或 checkpointer 不可用返回 503，Redis 降级只标记 degraded。"""
    database = await _check_database()
    redis = await _check_redis()
    checkpointer = _check_checkpointer()

    if not database["ok"] or not checkpointer["ok"]:
        status = "unavailable"
        status_code = 503
    elif not redis["ok"]:
        status = "degraded"
        status_code = 200
    else:
        status = "ready"
        status_code = 200

    return JSONResponse(
        status_code=status_code,
        content={
            "status": status,
            "checks": {
                "database": database,
                "redis": redis,
                "checkpointer": checkpointer,
            },
        },
    )


@metrics_router.get("")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")


@metrics_router.get("/json")
async def metrics_json() -> dict:
    return snapshot()
