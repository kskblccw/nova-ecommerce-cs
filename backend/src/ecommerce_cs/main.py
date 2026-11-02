"""FastAPI 应用入口。"""

import asyncio
import sys
from contextlib import asynccontextmanager, suppress
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ecommerce_cs.agents.graph import build_graph
from ecommerce_cs.api.routers import admin, auth, chat, conversations, health
from ecommerce_cs.core.errors import AppError, RateLimitError
from ecommerce_cs.core.logging import configure_logging, get_logger, set_request_id
from ecommerce_cs.core.observability import configure_tracing
from ecommerce_cs.core.ratelimit import close_rate_limit
from ecommerce_cs.core.settings import get_settings, validate_settings
from ecommerce_cs.db.session import dispose_engine
from ecommerce_cs.jobs.cleanup import cleanup_loop, run_cleanup
from ecommerce_cs.runtime import close_runtime, set_graph
from ecommerce_cs.services.memory import drain_background_tasks

# Windows 上 psycopg 异步连接需要 Selector 事件循环
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

log = get_logger("main")


async def _create_checkpointer():
    settings = get_settings()
    if settings.checkpointer_backend == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver(), None

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=settings.psycopg_conninfo,
        min_size=2,
        max_size=10,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    await pool.open()
    saver = AsyncPostgresSaver(cast(Any, pool))
    await saver.setup()
    return saver, pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    validate_settings(settings)
    configure_logging(settings.log_level)
    configure_tracing(settings)
    checkpointer, pool = await _create_checkpointer()
    graph = build_graph(checkpointer=checkpointer)
    set_graph(graph, checkpointer, pool)

    stop_cleanup = asyncio.Event()
    cleanup_task: asyncio.Task | None = None
    if settings.cleanup_on_startup:
        try:
            await run_cleanup()
        except Exception:
            log.exception("startup_cleanup_failed")
    cleanup_task = asyncio.create_task(cleanup_loop(stop_cleanup))

    log.info("app_started", env=settings.app_env)
    try:
        yield
    finally:
        stop_cleanup.set()
        if cleanup_task:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
        await drain_background_tasks()
        await close_runtime()
        await close_rate_limit()
        await dispose_engine()
        log.info("app_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = set_request_id(request.headers.get("X-Request-ID", ""))
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request_failed", path=request.url.path)
            raise
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimitError):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    prefix = settings.api_prefix
    app.include_router(health.router, prefix=prefix)
    app.include_router(health.metrics_router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(conversations.router, prefix=prefix)
    app.include_router(chat.router, prefix=prefix)
    app.include_router(chat.messages_router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)

    @app.get("/")
    async def root() -> dict:
        return {"service": settings.app_name, "version": "1.0.0", "docs": "/docs"}

    return app


app = create_app()
