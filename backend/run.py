"""Windows 友好的本地启动入口。

uvicorn 在 Windows 上会强制使用 ProactorEventLoop，而 psycopg3 异步连接
需要 SelectorEventLoop。这里绕过 ``uvicorn.run``，自己创建 Selector 循环，
并把 ``src`` 加入模块搜索路径，无需手动设置 PYTHONPATH。

用法（在 backend 目录）：
    python run.py
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import uvicorn


def _make_loop() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


if __name__ == "__main__":
    config = uvicorn.Config(
        "ecommerce_cs.main:app",
        host="0.0.0.0",
        port=8000,
        loop="asyncio",
        log_level="info",
    )
    server = uvicorn.Server(config)
    loop = _make_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()
