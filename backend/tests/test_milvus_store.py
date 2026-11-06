"""Milvus Lite 向量层测试：URI 归一化、写入、删除、检索与降级。"""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from ecommerce_cs.rag import milvus_store
from ecommerce_cs.rag.milvus_store import (
    ChunkVector,
    VectorHit,
    delete_documents,
    normalize_uri,
    search,
    upsert_chunks,
)


class _FakeClient:
    def __init__(self, *, search_result=None, error: bool = False):
        self.upserts: list[tuple[str, list[dict]]] = []
        self.deletes: list[tuple[str, str]] = []
        self.search_calls: list[dict] = []
        self.dropped: list[str] = []
        self.search_result = search_result
        self.error = error

    def upsert(self, collection, data):
        if self.error:
            raise RuntimeError("milvus down")
        self.upserts.append((collection, data))

    def delete(self, collection, filter):
        if self.error:
            raise RuntimeError("milvus down")
        self.deletes.append((collection, filter))

    def search(self, collection, data=None, limit=None, output_fields=None, search_params=None):
        if self.error:
            raise RuntimeError("milvus down")
        self.search_calls.append({"collection": collection, "limit": limit})
        if self.search_result is not None:
            return self.search_result
        return [
            [
                {
                    "id": "c1",
                    "distance": 0.91,
                    "entity": {"document_id": "d1", "chunk_index": 1},
                },
                {"id": "c2", "distance": 0.42, "entity": {"document_id": "d2", "chunk_index": 0}},
            ]
        ]

    def drop_collection(self, collection):
        self.dropped.append(collection)


class _FakeSchema:
    def __init__(self):
        self.fields: list[tuple[tuple, dict]] = []

    def add_field(self, *args, **kwargs):
        self.fields.append((args, kwargs))


class _FakeIndexParams:
    def add_index(self, **kwargs):
        return None


class _FakeMilvusClient:
    last: "_FakeMilvusClient | None" = None

    def __init__(self, uri, db_name=""):
        self.uri = uri
        self.db_name = db_name
        self.created: list[str] = []
        self.loaded: list[str] = []
        self.closed = False
        _FakeMilvusClient.last = self

    @staticmethod
    def create_schema(**_kwargs):
        return _FakeSchema()

    def has_collection(self, _name):
        return False

    def prepare_index_params(self):
        return _FakeIndexParams()

    def create_collection(self, name, schema=None, index_params=None):
        self.created.append(name)

    def load_collection(self, name):
        self.loaded.append(name)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(milvus_store, "_available", True)
    monkeypatch.setattr(milvus_store, "_get_client", lambda: client)
    monkeypatch.setattr(
        milvus_store,
        "get_settings",
        lambda: SimpleNamespace(
            rag_vector_backend="milvus", milvus_collection="rag_chunks", embedding_dim=1024
        ),
    )
    return client


def test_normalize_uri_uses_forward_slashes_for_local_paths():
    assert normalize_uri(r"C:\data\milvus.db") == "C:/data/milvus.db"
    assert normalize_uri("http://milvus:19530") == "http://milvus:19530"
    assert normalize_uri("") == ""


@pytest.mark.parametrize("backend", ["numpy", "pgvector"])
def test_available_respects_explicit_backend(monkeypatch, backend):
    monkeypatch.setattr(
        milvus_store,
        "get_settings",
        lambda: SimpleNamespace(rag_vector_backend=backend, milvus_uri="x", milvus_collection="c"),
    )
    assert milvus_store.available() is False


def test_available_caches_failure(monkeypatch):
    monkeypatch.setattr(
        milvus_store,
        "get_settings",
        lambda: SimpleNamespace(
            rag_vector_backend="milvus", milvus_uri="x", milvus_collection="c", embedding_dim=4
        ),
    )

    def broken():
        raise RuntimeError("cannot open milvus")

    monkeypatch.setattr(milvus_store, "_get_client", broken)
    milvus_store.reset()

    assert milvus_store.available() is False
    # 第二次直接读缓存，不再重复初始化
    assert milvus_store.available() is False


def test_upsert_chunks_skips_empty_vectors(fake_client):
    written = upsert_chunks(
        [
            ChunkVector("c1", "d1", 0, [0.1, 0.2]),
            ChunkVector("c2", "d1", 1, []),
        ]
    )

    assert written == 1
    assert len(fake_client.upserts) == 1
    collection, data = fake_client.upserts[0]
    assert collection == "rag_chunks"
    assert data == [{"id": "c1", "document_id": "d1", "chunk_index": 0, "vector": [0.1, 0.2]}]


def test_upsert_chunks_returns_zero_when_unavailable(monkeypatch):
    monkeypatch.setattr(milvus_store, "_available", False)
    assert upsert_chunks([ChunkVector("c1", "d1", 0, [0.1])]) == 0


def test_upsert_failure_marks_unavailable(monkeypatch, fake_client):
    fake_client.error = True
    assert upsert_chunks([ChunkVector("c1", "d1", 0, [0.1])]) == 0
    assert milvus_store.available() is False


def test_delete_documents_dedupes_and_builds_filter(fake_client):
    removed = delete_documents(["d1", "d1", " d2 ", ""])

    assert removed == 2
    assert fake_client.deletes == [("rag_chunks", 'document_id in ["d1", "d2"]')]


def test_delete_documents_escapes_quotes(fake_client):
    delete_documents(['d"1'])
    assert fake_client.deletes[0][1] == 'document_id in ["d\\"1"]'


def test_delete_documents_batches_large_input(fake_client, monkeypatch):
    monkeypatch.setattr(milvus_store, "_DELETE_BATCH", 2)
    removed = delete_documents(["d1", "d2", "d3"])

    assert removed == 3
    assert len(fake_client.deletes) == 2


def test_search_parses_hits_and_skips_missing_document(fake_client):
    fake_client.search_result = [
        [
            {"id": "c1", "distance": 0.9, "entity": {"document_id": "d1", "chunk_index": 1}},
            {"id": "c2", "distance": 0.8, "entity": {}},
        ]
    ]

    hits = search([1.0, 0.0], limit=2)

    assert hits == [VectorHit("c1", "d1", 1, 0.9)]
    assert fake_client.search_calls[0]["limit"] == 2


def test_search_returns_empty_on_error(monkeypatch, fake_client):
    fake_client.error = True
    assert search([1.0], limit=3) == []
    assert milvus_store.available() is False


def test_search_returns_empty_for_empty_vector(fake_client):
    assert search([], limit=3) == []
    assert fake_client.search_calls == []


def test_reset_clears_cached_state(monkeypatch):
    monkeypatch.setattr(
        milvus_store,
        "get_settings",
        lambda: SimpleNamespace(
            rag_vector_backend="numpy", milvus_db_uri="x", milvus_collection="c", embedding_dim=4
        ),
    )
    milvus_store.disable()
    assert milvus_store.available() is False

    milvus_store.reset()
    # backend=numpy 时无论缓存如何都不可用
    assert milvus_store.available() is False


def test_get_client_creates_collection_and_loads(monkeypatch):
    # 用假模块替代真实 pymilvus：它在导入时会把项目 .env 全量写入 os.environ
    fake_module = ModuleType("pymilvus")
    fake_module.MilvusClient = _FakeMilvusClient
    fake_module.DataType = SimpleNamespace(VARCHAR="VARCHAR", INT64="INT64", FLOAT_VECTOR="FLOAT")
    monkeypatch.setitem(sys.modules, "pymilvus", fake_module)
    monkeypatch.setattr(
        milvus_store,
        "get_settings",
        lambda: SimpleNamespace(
            rag_vector_backend="milvus",
            milvus_db_uri=r"C:\Users\x\nova.db",
            milvus_collection="rag_chunks",
            embedding_dim=8,
        ),
    )
    milvus_store.reset()

    client = milvus_store._get_client()

    assert client.uri == "C:/Users/x/nova.db"
    assert client.db_name == "default"
    assert client.created == ["rag_chunks"]
    assert client.loaded == ["rag_chunks"]
    # 单例复用：第二次不再重复建集合
    assert milvus_store._get_client() is client


def test_available_returns_true_after_successful_init(monkeypatch):
    monkeypatch.setattr(milvus_store, "_get_client", lambda: _FakeMilvusClient("x"))
    monkeypatch.setattr(
        milvus_store,
        "get_settings",
        lambda: SimpleNamespace(rag_vector_backend="milvus"),
    )
    milvus_store.reset()

    assert milvus_store.available() is True
    assert milvus_store.available() is True


def test_reset_closes_client(monkeypatch):
    client = _FakeMilvusClient("x")
    monkeypatch.setattr(milvus_store, "_client", client)

    milvus_store.reset()

    assert client.closed is True
    assert milvus_store._client is None
