"""分布式限流与登录失败锁定。

优先使用 Redis（多实例/多 worker 场景下计数一致）；未配置 REDIS_URL 或
Redis 暂时不可用时，自动降级为进程内实现，保证主流程不被限流组件拖垮。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from ecommerce_cs.core.errors import RateLimitError
from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.core.security import hash_phone, hash_token
from ecommerce_cs.core.settings import get_settings

log = get_logger("ratelimit")

_NO_REDIS = object()
_REDIS_RETRY_SECONDS = 30
_SLOT_TTL_SECONDS = 600

SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = window
  if oldest[2] then
    retry = math.max(1, math.ceil(window - (now - tonumber(oldest[2])))
  end
  return {0, retry}
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, math.ceil(window * 1000))
return {1, 0}
"""

ACQUIRE_SLOT_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
if current > tonumber(ARGV[2]) then
  redis.call('DECR', KEYS[1])
  return 0
end
return 1
"""

RELEASE_SLOT_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return 0
end
local current = redis.call('DECR', KEYS[1])
if current <= 0 then
  redis.call('DEL', KEYS[1])
end
return current
"""

FAIL_LOCK_LUA = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
if count >= tonumber(ARGV[1]) then
  return redis.call('TTL', KEYS[1])
end
return 0
"""

FAIL_INCREMENT_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class SlidingWindowLimiter:
    """进程内滑动窗口计数器（Redis 不可用时的降级实现）。"""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = {}

    def _prune(self, key: str, window: float, now: float) -> deque[float] | None:
        events = self._events.get(key)
        if not events:
            return None
        while events and events[0] <= now - window:
            events.popleft()
        if not events:
            self._events.pop(key, None)
            return None
        return events

    def hit(self, key: str, *, limit: int, window_seconds: float) -> tuple[bool, int]:
        now = time.monotonic()
        events = self._prune(key, window_seconds, now)
        if events is None:
            events = deque()
            self._events[key] = events
        if len(events) >= limit:
            retry_after = int(window_seconds - (now - events[0])) + 1
            return False, max(1, retry_after)
        events.append(now)
        if len(self._events) > 5000:
            self._sweep(now)
        return True, 0

    def _sweep(self, now: float) -> None:
        for key in list(self._events):
            events = self._events.get(key)
            if not events:
                self._events.pop(key, None)
                continue
            while events and events[0] <= now - 86400:
                events.popleft()
            if not events:
                self._events.pop(key, None)

    def clear(self) -> None:
        self._events.clear()


@dataclass
class _FailureState:
    count: int = 0
    locked_until: float = 0.0


class FailureLock:
    """进程内登录失败计数。"""

    def __init__(self) -> None:
        self._states: dict[str, _FailureState] = {}

    def locked_seconds(self, key: str) -> int:
        state = self._states.get(key)
        if not state:
            return 0
        now = time.monotonic()
        if state.locked_until > now:
            return int(state.locked_until - now) + 1
        if state.locked_until:
            self._states.pop(key, None)
        return 0

    def fail(
        self, key: str, limit: int | None = None, lock_minutes: int | None = None
    ) -> int:
        state = self._states.setdefault(key, _FailureState())
        state.count += 1
        settings = get_settings()
        threshold = limit or settings.login_max_failures
        if state.count >= threshold:
            minutes = lock_minutes or settings.login_lock_minutes
            state.locked_until = time.monotonic() + minutes * 60
        return state.count

    def reset(self, key: str) -> None:
        self._states.pop(key, None)

    def clear(self) -> None:
        self._states.clear()


class RateLimitService:
    def __init__(self) -> None:
        self._redis: Any = None
        self._redis_down_until = 0.0
        self._redis_lock = asyncio.Lock()
        self._window = SlidingWindowLimiter()
        self._failures = FailureLock()
        self._slots: dict[int, int] = {}

    async def _client(self):
        settings = get_settings()
        if not settings.redis_url or time.monotonic() < self._redis_down_until:
            return None
        if self._redis is not None:
            return self._redis
        async with self._redis_lock:
            if self._redis is not None:
                return self._redis
            try:
                from redis.asyncio import Redis

                self._redis = Redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_timeout=settings.rate_limit_redis_timeout_seconds,
                    socket_connect_timeout=settings.rate_limit_redis_timeout_seconds,
                )
            except Exception:
                log.exception("rate_limit_redis_init_failed")
                self._redis_down_until = time.monotonic() + _REDIS_RETRY_SECONDS
                return None
        return self._redis

    async def _run_redis(self, operation):
        client = await self._client()
        if client is None:
            return _NO_REDIS
        settings = get_settings()
        try:
            return await asyncio.wait_for(
                operation(client), timeout=settings.rate_limit_redis_timeout_seconds
            )
        except Exception:
            self._redis_down_until = time.monotonic() + _REDIS_RETRY_SECONDS
            log.warning("rate_limit_redis_unavailable_fallback_memory")
            return _NO_REDIS

    def _key(self, suffix: str) -> str:
        return f"{get_settings().rate_limit_key_prefix}:{suffix}"

    async def hit(self, key: str, *, limit: int, window_seconds: float) -> tuple[bool, int]:
        redis_key = self._key(f"win:{key}")
        result = await self._run_redis(
            lambda client: client.eval(
                SLIDING_WINDOW_LUA,
                1,
                redis_key,
                time.time(),
                window_seconds,
                limit,
                f"{time.time()}:{uuid.uuid4().hex[:8]}",
            )
        )
        if result is not _NO_REDIS:
            return bool(int(result[0])), int(result[1])
        return self._window.hit(key, limit=limit, window_seconds=window_seconds)

    async def _locked_for(self, key: str, limit: int) -> int:
        result = await self._run_redis(
            lambda client: client.eval(FAIL_LOCK_LUA, 1, key, limit)
        )
        if result is not _NO_REDIS:
            return max(0, int(result))
        return self._failures.locked_seconds(key)

    async def _fail_for(
        self, key: str, limit: int, window_seconds: int, lock_minutes: int
    ) -> int:
        result = await self._run_redis(
            lambda client: client.eval(FAIL_INCREMENT_LUA, 1, key, window_seconds)
        )
        if result is not _NO_REDIS:
            return int(result)
        return self._failures.fail(key, limit=limit, lock_minutes=lock_minutes)

    async def _reset_for(self, key: str) -> None:
        result = await self._run_redis(lambda client: client.delete(key))
        if result is _NO_REDIS:
            self._failures.reset(key)

    async def locked_seconds(self, phone: str) -> int:
        settings = get_settings()
        return await self._locked_for(
            self._key(f"fail:{hash_phone(phone)}"), settings.login_max_failures
        )

    async def fail(self, phone: str) -> int:
        settings = get_settings()
        return await self._fail_for(
            self._key(f"fail:{hash_phone(phone)}"),
            settings.login_max_failures,
            settings.login_lock_minutes * 60,
            settings.login_lock_minutes,
        )

    async def reset_fail(self, phone: str) -> None:
        await self._reset_for(self._key(f"fail:{hash_phone(phone)}"))

    async def ip_locked_seconds(self, ip: str) -> int:
        settings = get_settings()
        return await self._locked_for(
            self._key(f"failip:{hash_token(ip)}"), settings.login_max_ip_failures
        )

    async def ip_fail(self, ip: str) -> int:
        settings = get_settings()
        return await self._fail_for(
            self._key(f"failip:{hash_token(ip)}"),
            settings.login_max_ip_failures,
            settings.login_ip_lock_minutes * 60,
            settings.login_ip_lock_minutes,
        )

    async def acquire_slot(self, user_id: int, limit: int) -> bool:
        key = self._key(f"slot:{user_id}")
        result = await self._run_redis(
            lambda client: client.eval(
                ACQUIRE_SLOT_LUA, 1, key, _SLOT_TTL_SECONDS, limit
            )
        )
        if result is not _NO_REDIS:
            return bool(int(result))
        current = self._slots.get(user_id, 0)
        if current >= limit:
            return False
        self._slots[user_id] = current + 1
        return True

    async def release_slot(self, user_id: int) -> None:
        key = self._key(f"slot:{user_id}")
        result = await self._run_redis(
            lambda client: client.eval(RELEASE_SLOT_LUA, 1, key)
        )
        if result is not _NO_REDIS:
            return
        current = self._slots.get(user_id, 0)
        if current <= 1:
            self._slots.pop(user_id, None)
        else:
            self._slots[user_id] = current - 1

    def clear_memory(self) -> None:
        self._window.clear()
        self._failures.clear()
        self._slots.clear()

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None


service = RateLimitService()


def client_ip(request: Request) -> str:
    settings = get_settings()
    forwarded = request.headers.get("X-Forwarded-For", "")
    if settings.trust_forwarded_for and forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


async def _enforce(key: str, *, limit: int, window: float) -> None:
    if not get_settings().rate_limit_enabled:
        return
    allowed, retry_after = await service.hit(key, limit=limit, window_seconds=window)
    if not allowed:
        raise RateLimitError(
            f"操作过于频繁，请 {retry_after} 秒后再试",
            retry_after=retry_after,
        )


async def enforce_login_rate(request: Request) -> None:
    await _enforce(
        f"login:{client_ip(request)}",
        limit=get_settings().rate_limit_login_per_minute,
        window=60,
    )


async def enforce_register_rate(request: Request) -> None:
    await _enforce(
        f"register:{client_ip(request)}",
        limit=get_settings().rate_limit_register_per_hour,
        window=3600,
    )


async def ensure_login_allowed(phone: str, ip: str = "") -> None:
    remaining = await service.locked_seconds(phone)
    if remaining:
        minutes = max(1, (remaining + 59) // 60)
        raise RateLimitError(
            f"登录失败次数过多，请 {minutes} 分钟后再试",
            retry_after=remaining,
        )
    if ip:
        ip_remaining = await service.ip_locked_seconds(ip)
        if ip_remaining:
            minutes = max(1, (ip_remaining + 59) // 60)
            raise RateLimitError(
                f"当前网络登录失败次数过多，请 {minutes} 分钟后再试",
                retry_after=ip_remaining,
            )


async def record_login_failure(phone: str, ip: str = "") -> None:
    settings = get_settings()
    count = await service.fail(phone)
    ip_count = await service.ip_fail(ip) if ip else 0
    if count >= settings.login_max_failures:
        raise RateLimitError(
            f"登录失败次数过多，请 {settings.login_lock_minutes} 分钟后再试",
            retry_after=settings.login_lock_minutes * 60,
        )
    if ip and ip_count >= settings.login_max_ip_failures:
        raise RateLimitError(
            f"当前网络登录失败次数过多，请 {settings.login_ip_lock_minutes} 分钟后再试",
            retry_after=settings.login_ip_lock_minutes * 60,
        )


async def reset_login_failure(phone: str) -> None:
    await service.reset_fail(phone)


async def enforce_chat_rate(user_id: int) -> None:
    settings = get_settings()
    await _enforce(
        f"chat:m:{user_id}",
        limit=settings.rate_limit_chat_per_minute,
        window=60,
    )
    await _enforce(
        f"chat:d:{user_id}",
        limit=settings.rate_limit_chat_per_day,
        window=86400,
    )


async def acquire_stream_slot(user_id: int) -> None:
    limit = max(1, get_settings().max_concurrent_streams_per_user)
    if not await service.acquire_slot(user_id, limit):
        raise RateLimitError("上一条回复还在生成中，请稍候或点击停止", retry_after=15)


async def release_stream_slot(user_id: int) -> None:
    await service.release_slot(user_id)


async def close_rate_limit() -> None:
    await service.close()


def reset_state() -> None:
    """测试复位：清空进程内计数。"""
    service.clear_memory()
