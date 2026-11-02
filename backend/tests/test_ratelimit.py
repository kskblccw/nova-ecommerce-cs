"""限流、登录锁定与并发槽位测试。"""

import pytest

from ecommerce_cs.core import ratelimit
from ecommerce_cs.core.errors import RateLimitError


@pytest.fixture(autouse=True)
def _reset():
    ratelimit.reset_state()
    yield
    ratelimit.reset_state()


@pytest.mark.asyncio
async def test_sliding_window_blocks_after_limit():
    allowed, _ = await ratelimit.service.hit("k", limit=2, window_seconds=60)
    assert allowed
    allowed, _ = await ratelimit.service.hit("k", limit=2, window_seconds=60)
    assert allowed
    allowed, retry_after = await ratelimit.service.hit("k", limit=2, window_seconds=60)
    assert not allowed
    assert retry_after >= 1


@pytest.mark.asyncio
async def test_login_lock_after_repeated_failures():
    phone = "13800001001"
    for _ in range(4):
        await ratelimit.record_login_failure(phone)
    assert await ratelimit.service.locked_seconds(phone) == 0
    with pytest.raises(RateLimitError):
        await ratelimit.record_login_failure(phone)
    assert await ratelimit.service.locked_seconds(phone) > 0
    with pytest.raises(RateLimitError):
        await ratelimit.ensure_login_allowed(phone)
    await ratelimit.reset_login_failure(phone)
    await ratelimit.ensure_login_allowed(phone)


@pytest.mark.asyncio
async def test_stream_slot_rejects_duplicate_generation():
    user_id = 7
    await ratelimit.acquire_stream_slot(user_id)
    with pytest.raises(RateLimitError):
        await ratelimit.acquire_stream_slot(user_id)
    await ratelimit.release_stream_slot(user_id)
    await ratelimit.acquire_stream_slot(user_id)


@pytest.mark.asyncio
async def test_ip_lock_after_repeated_failures():
    ip = "203.0.113.10"
    for index in range(19):
        await ratelimit.record_login_failure(f"1380000{index:04d}", ip)

    with pytest.raises(RateLimitError):
        await ratelimit.record_login_failure("13800009999", ip)

    assert await ratelimit.service.ip_locked_seconds(ip) > 0
    with pytest.raises(RateLimitError):
        await ratelimit.ensure_login_allowed("13800001001", ip)
