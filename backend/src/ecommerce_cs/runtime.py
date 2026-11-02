"""应用级运行时单例（Graph / Checkpointer）。"""

from typing import Any

_graph: Any = None
_checkpointer: Any = None
_pool: Any = None


def set_graph(graph: Any, checkpointer: Any = None, pool: Any = None) -> None:
    global _graph, _checkpointer, _pool
    _graph = graph
    _checkpointer = checkpointer
    _pool = pool


def get_graph() -> Any:
    if _graph is None:
        raise RuntimeError("Graph 尚未初始化")
    return _graph


def get_checkpointer() -> Any:
    return _checkpointer


async def close_runtime() -> None:
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            pass
