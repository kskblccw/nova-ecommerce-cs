"""定时清理测试：审批过期、会话回收与 checkpoint 清理。"""

import asyncio
from types import SimpleNamespace

import pytest

from ecommerce_cs.jobs import cleanup as cleanup_module


class _Result:
    def __init__(self, rows=(), rowcount: int = 0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _stmt, _params=None):
        if self.results:
            return self.results.pop(0)
        return _Result()

    async def commit(self):
        self.committed += 1


class _FakeConnection:
    def __init__(self, rowcounts, fail: bool = False):
        self.rowcounts = list(rowcounts)
        self.fail = fail
        self.calls: list[tuple[str, object]] = []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self.fail:
            raise RuntimeError("checkpoint table missing")
        return _Result(rowcount=self.rowcounts.pop(0) if self.rowcounts else 1)


class _FakeEngine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_args):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_run_cleanup_aggregates_all_counters(monkeypatch):
    calls: dict[str, object] = {}

    async def fake_delete_threads(thread_ids):
        calls["thread_ids"] = thread_ids
        return 7

    async def fake_checkpoints(cutoff):
        calls["cutoff"] = cutoff
        return 9

    monkeypatch.setattr(cleanup_module, "_delete_threads", fake_delete_threads)
    monkeypatch.setattr(cleanup_module, "_cleanup_checkpoints", fake_checkpoints)
    monkeypatch.setattr(
        cleanup_module,
        "SessionLocal",
        lambda: _FakeSession(
            [
                _Result([("c1",), ("c2",)]),
                _Result([("c3",)]),
                _Result(rowcount=4),
                _Result(rowcount=5),
            ]
        ),
    )

    stats = await cleanup_module.run_cleanup()

    assert stats == {
        "expired_actions": 2,
        "stale_executions": 1,
        "approval_threads": 7,
        "auth_sessions": 4,
        "retrieval_logs": 5,
        "checkpoints": 9,
    }
    assert calls["thread_ids"] == {"conv:c1", "conv:c2", "conv:c3"}


@pytest.mark.asyncio
async def test_run_cleanup_skips_thread_cleanup_without_approvals(monkeypatch):
    async def fake_delete_threads(thread_ids):
        assert thread_ids == set()
        return 0

    async def fake_checkpoints(_cutoff):
        return 0

    monkeypatch.setattr(cleanup_module, "_delete_threads", fake_delete_threads)
    monkeypatch.setattr(cleanup_module, "_cleanup_checkpoints", fake_checkpoints)
    monkeypatch.setattr(
        cleanup_module,
        "SessionLocal",
        lambda: _FakeSession([_Result(), _Result(), _Result(), _Result()]),
    )

    stats = await cleanup_module.run_cleanup()

    assert stats["expired_actions"] == 0
    assert stats["approval_threads"] == 0


@pytest.mark.asyncio
async def test_delete_threads_is_noop_for_memory_checkpointer(monkeypatch):
    monkeypatch.setattr(
        cleanup_module,
        "get_settings",
        lambda: SimpleNamespace(checkpointer_backend="memory"),
    )

    assert await cleanup_module._delete_threads({"conv:c1"}) == 0


@pytest.mark.asyncio
async def test_delete_threads_is_noop_for_empty_set(monkeypatch):
    monkeypatch.setattr(
        cleanup_module,
        "get_settings",
        lambda: SimpleNamespace(checkpointer_backend="postgres"),
    )

    assert await cleanup_module._delete_threads(set()) == 0


@pytest.mark.asyncio
async def test_delete_threads_removes_rows_from_all_checkpoint_tables(monkeypatch):
    monkeypatch.setattr(
        cleanup_module,
        "get_settings",
        lambda: SimpleNamespace(checkpointer_backend="postgres"),
    )
    conn = _FakeConnection([2, 3, 4])
    monkeypatch.setattr(cleanup_module, "engine", _FakeEngine(conn))

    removed = await cleanup_module._delete_threads({"conv:c1", "conv:c2"})

    assert removed == 9
    assert len(conn.calls) == len(cleanup_module.CHECKPOINT_TABLES)
    assert set(conn.calls[0][1].values()) == {"conv:c1", "conv:c2"}


@pytest.mark.asyncio
async def test_delete_threads_swallows_engine_errors(monkeypatch):
    monkeypatch.setattr(
        cleanup_module,
        "get_settings",
        lambda: SimpleNamespace(checkpointer_backend="postgres"),
    )
    monkeypatch.setattr(
        cleanup_module, "engine", _FakeEngine(_FakeConnection([1], fail=True))
    )

    assert await cleanup_module._delete_threads({"conv:c1"}) == 0


@pytest.mark.asyncio
async def test_cleanup_checkpoints_is_noop_for_memory_checkpointer(monkeypatch):
    monkeypatch.setattr(
        cleanup_module,
        "get_settings",
        lambda: SimpleNamespace(checkpointer_backend="memory"),
    )

    assert await cleanup_module._cleanup_checkpoints(None) == 0


@pytest.mark.asyncio
async def test_cleanup_checkpoints_sums_rowcounts(monkeypatch):
    monkeypatch.setattr(
        cleanup_module,
        "get_settings",
        lambda: SimpleNamespace(checkpointer_backend="postgres"),
    )
    conn = _FakeConnection([5, 6, 7])
    monkeypatch.setattr(cleanup_module, "engine", _FakeEngine(conn))

    removed = await cleanup_module._cleanup_checkpoints(None)

    assert removed == 18
    assert len(conn.calls) == len(cleanup_module.CHECKPOINT_TABLES)


@pytest.mark.asyncio
async def test_cleanup_checkpoints_swallows_engine_errors(monkeypatch):
    monkeypatch.setattr(
        cleanup_module,
        "get_settings",
        lambda: SimpleNamespace(checkpointer_backend="postgres"),
    )
    monkeypatch.setattr(
        cleanup_module, "engine", _FakeEngine(_FakeConnection([1], fail=True))
    )

    assert await cleanup_module._cleanup_checkpoints(None) == 0


@pytest.mark.asyncio
async def test_cleanup_loop_runs_once_then_stops(monkeypatch):
    runs: list[int] = []

    async def fake_cleanup():
        runs.append(1)

    monkeypatch.setattr(cleanup_module, "run_cleanup", fake_cleanup)
    stop = asyncio.Event()
    calls = {"count": 0}

    async def fake_wait_for(awaitable, timeout):
        calls["count"] += 1
        awaitable.close()
        if calls["count"] == 1:
            raise TimeoutError
        stop.set()
        return True

    monkeypatch.setattr(
        cleanup_module, "asyncio", SimpleNamespace(wait_for=fake_wait_for)
    )

    await cleanup_module.cleanup_loop(stop)

    assert runs == [1]


@pytest.mark.asyncio
async def test_cleanup_loop_survives_cleanup_errors(monkeypatch):
    async def broken_cleanup():
        raise RuntimeError("db down")

    monkeypatch.setattr(cleanup_module, "run_cleanup", broken_cleanup)
    stop = asyncio.Event()
    calls = {"count": 0}

    async def fake_wait_for(awaitable, timeout):
        calls["count"] += 1
        awaitable.close()
        if calls["count"] == 1:
            raise TimeoutError
        stop.set()
        return True

    monkeypatch.setattr(
        cleanup_module, "asyncio", SimpleNamespace(wait_for=fake_wait_for)
    )

    await cleanup_module.cleanup_loop(stop)
    assert calls["count"] == 2
