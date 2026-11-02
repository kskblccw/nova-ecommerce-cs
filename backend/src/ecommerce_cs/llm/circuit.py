"""LLM 调用护栏：并发上限 + 熔断器 + 超时。

熔断打开期间直接抛出 ``LLMUnavailable``，由上层返回确定性降级话术，
避免故障时请求继续堆积把 LLM 与数据库连接池打满。
"""

from __future__ import annotations

import asyncio
import time

from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.core.settings import get_settings

log = get_logger("llm_circuit")


class LLMUnavailable(Exception):
    """LLM 熔断或并发队列已满，调用方应走降级路径。"""


class CircuitBreaker:
    def __init__(self) -> None:
        self.failures = 0
        self.opened_until = 0.0

    @property
    def is_open(self) -> bool:
        if not self.opened_until:
            return False
        if time.monotonic() >= self.opened_until:
            self.opened_until = 0.0
            self.failures = 0
            log.info("llm_circuit_closed")
            return False
        return True

    def success(self) -> None:
        self.failures = 0
        self.opened_until = 0.0

    def failure(self) -> None:
        settings = get_settings()
        self.failures += 1
        if self.failures >= settings.llm_failure_threshold and not self.opened_until:
            self.opened_until = time.monotonic() + settings.llm_cooldown_seconds
            log.warning(
                "llm_circuit_opened",
                failures=self.failures,
                cooldown_seconds=settings.llm_cooldown_seconds,
            )


circuit = CircuitBreaker()

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, get_settings().llm_max_concurrency))
    return _semaphore


def ensure_available() -> None:
    if circuit.is_open:
        raise LLMUnavailable()


async def run_llm(awaitable, *, timeout: float | None = None):
    """在并发与熔断护栏下执行一次 LLM 调用。"""
    ensure_available()
    settings = get_settings()
    semaphore = _get_semaphore()
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=5)
    except TimeoutError as exc:
        raise LLMUnavailable("LLM 并发队列已满") from exc

    try:
        result = await asyncio.wait_for(
            awaitable, timeout=timeout or settings.llm_timeout_seconds
        )
    except Exception:
        circuit.failure()
        raise
    else:
        circuit.success()
        return result
    finally:
        semaphore.release()
