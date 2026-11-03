"""LLM 护栏与 Embedding 工厂测试。"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from ecommerce_cs.llm import circuit as circuit_module
from ecommerce_cs.llm import factory as factory_module
from ecommerce_cs.llm.circuit import LLMUnavailable


def _settings(**overrides):
    values = {
        "llm_failure_threshold": 2,
        "llm_cooldown_seconds": 60,
        "llm_max_concurrency": 1,
        "llm_timeout_seconds": 10,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _reset_circuit(monkeypatch):
    monkeypatch.setattr(circuit_module.circuit, "failures", 0)
    monkeypatch.setattr(circuit_module.circuit, "opened_until", 0.0)


def test_circuit_opens_after_threshold_and_recovers(monkeypatch):
    monkeypatch.setattr(circuit_module, "get_settings", lambda: _settings())

    assert circuit_module.circuit.is_open is False
    circuit_module.circuit.failure()
    assert circuit_module.circuit.failures == 1
    assert circuit_module.circuit.is_open is False

    circuit_module.circuit.failure()
    assert circuit_module.circuit.is_open is True
    with pytest.raises(LLMUnavailable):
        circuit_module.ensure_available()

    monkeypatch.setattr(circuit_module.time, "monotonic", lambda: 1_000_000.0)
    assert circuit_module.circuit.is_open is False
    assert circuit_module.circuit.failures == 0


def test_circuit_success_resets_state():
    circuit_module.circuit.failures = 5
    circuit_module.circuit.opened_until = 123.0

    circuit_module.circuit.success()

    assert circuit_module.circuit.failures == 0
    assert circuit_module.circuit.opened_until == 0.0


@pytest.mark.asyncio
async def test_run_llm_returns_result(monkeypatch):
    monkeypatch.setattr(circuit_module, "get_settings", lambda: _settings())
    monkeypatch.setattr(circuit_module, "_semaphore", None)

    async def job():
        return "answer"

    assert await circuit_module.run_llm(job()) == "answer"
    assert circuit_module.circuit.failures == 0


@pytest.mark.asyncio
async def test_run_llm_records_failure_and_reraises(monkeypatch):
    monkeypatch.setattr(circuit_module, "get_settings", lambda: _settings())
    monkeypatch.setattr(circuit_module, "_semaphore", None)

    async def job():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await circuit_module.run_llm(job())

    assert circuit_module.circuit.failures == 1


@pytest.mark.asyncio
async def test_run_llm_cancels_slow_call_on_timeout(monkeypatch):
    monkeypatch.setattr(circuit_module, "get_settings", lambda: _settings())
    monkeypatch.setattr(circuit_module, "_semaphore", None)

    async def slow():
        await asyncio.sleep(5)

    with pytest.raises(TimeoutError):
        await circuit_module.run_llm(slow(), timeout=0.01)

    assert circuit_module.circuit.failures == 1


class _FakeResponse:
    def __init__(self, payload, error: Exception | None = None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


class _FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return self.responses.pop(0)


def _payload(*vectors):
    return {"output": {"embeddings": [{"embedding": vector} for vector in vectors]}}


def test_embed_documents_returns_empty_for_empty_input(monkeypatch):
    embeddings = factory_module.DashScopeEmbeddings("m", "k")

    assert asyncio.run(embeddings.embed_documents([])) == []


def test_embed_documents_batches_requests(monkeypatch):
    responses = [_FakeResponse(_payload([0.1])), _FakeResponse(_payload([0.2]))]
    client = _FakeAsyncClient(responses)
    monkeypatch.setattr(factory_module.httpx, "AsyncClient", client)

    embeddings = factory_module.DashScopeEmbeddings("m", "k")
    vectors = asyncio.run(embeddings.embed_documents([f"t{i}" for i in range(11)]))

    assert vectors == [[0.1], [0.2]]
    assert len(client.posts) == 2
    assert len(client.posts[0]["json"]["input"]["texts"]) == 10


def test_embed_documents_retries_http_errors(monkeypatch):
    responses = [
        _FakeResponse({}, error=httpx.HTTPError("boom")),
        _FakeResponse(_payload([0.3])),
    ]
    client = _FakeAsyncClient(responses)
    monkeypatch.setattr(factory_module.httpx, "AsyncClient", client)

    embeddings = factory_module.DashScopeEmbeddings("m", "k")
    assert asyncio.run(embeddings.embed_documents(["文本"])) == [[0.3]]
    assert len(client.posts) == 2


def test_embed_documents_raises_after_three_failures(monkeypatch):
    responses = [
        _FakeResponse({}, error=httpx.HTTPError("boom"))
        for _ in range(3)
    ]
    client = _FakeAsyncClient(responses)
    monkeypatch.setattr(factory_module.httpx, "AsyncClient", client)

    embeddings = factory_module.DashScopeEmbeddings("m", "k")
    with pytest.raises(httpx.HTTPError):
        asyncio.run(embeddings.embed_documents(["文本"]))


def test_embed_query_returns_first_vector(monkeypatch):
    client = _FakeAsyncClient([_FakeResponse(_payload([1.0, 2.0]))])
    monkeypatch.setattr(factory_module.httpx, "AsyncClient", client)

    embeddings = factory_module.DashScopeEmbeddings("m", "k")
    assert asyncio.run(embeddings.embed_query("查询")) == [1.0, 2.0]
