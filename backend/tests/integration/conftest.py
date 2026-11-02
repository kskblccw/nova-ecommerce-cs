"""集成测试公共夹具：数据库不可用时跳过，避免本地未启动 PostgreSQL 时误报。"""

import pytest
from sqlalchemy import text

from ecommerce_cs.db.session import SessionLocal, engine


@pytest.fixture(autouse=True)
async def _dispose_engine():
    """每个测试结束释放连接池，避免连接跨事件循环复用。"""
    yield
    await engine.dispose()


@pytest.fixture
async def db_ready():
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"数据库不可用，跳过集成测试：{exc}")
