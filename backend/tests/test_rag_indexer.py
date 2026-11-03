"""知识库索引测试：文档构建、切片写入、向量降级与重建。"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from ecommerce_cs.db.models import FAQ, Product, RagDocument
from ecommerce_cs.rag import indexer as indexer_module
from ecommerce_cs.rag.indexer import (
    _build_system_documents,
    _format_specs,
    _index_documents,
    _system_id,
    _write_vectors,
    index_single_document,
    rebuild_index,
)


class _Result:
    def __init__(self, rows=(), rowcount: int = 0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, *, products=(), faqs=(), documents=(), rowcount: int = 0):
        self.products = list(products)
        self.faqs = list(faqs)
        self.documents = list(documents)
        self.rowcount = rowcount
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0
        self.executed: list = []

    async def scalars(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is Product:
            return _Result(self.products)
        if entity is FAQ:
            return _Result(self.faqs)
        if entity is RagDocument:
            return _Result(self.documents)
        return _Result([])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return _Result(rowcount=self.rowcount)


class _FakeEmbeddings:
    def __init__(self, vectors=None, error: Exception | None = None):
        self.vectors = vectors
        self.error = error

    async def embed_documents(self, texts):
        if self.error:
            raise self.error
        if self.vectors is not None:
            return self.vectors
        return [[0.1, 0.2] for _ in texts]


def _product(product_id: int, name: str = "测试耳机") -> SimpleNamespace:
    return SimpleNamespace(
        id=product_id,
        name=name,
        description="主动降噪耳机",
        price=599.0,
        stock=20,
        category="手机数码",
        specs={"颜色": "黑色"},
    )


def _faq(faq_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=faq_id, question="如何退货", answer="签收 7 天内可申请", category="售后")


def test_system_id_is_stable_and_hex():
    first = _system_id("product", 1)
    second = _system_id("product", 1)
    other = _system_id("product", 2)

    assert first == second
    assert first != other
    assert len(first) == 32


def test_format_specs_handles_empty_and_pairs():
    assert _format_specs(None) == "暂无详细参数"
    assert _format_specs({}) == "暂无详细参数"
    assert _format_specs({"颜色": "黑色", "内存": "256G"}) == "颜色：黑色；内存：256G"


@pytest.mark.asyncio
async def test_build_system_documents_covers_all_sources():
    db = _FakeDB(products=[_product(1)], faqs=[_faq(2)])

    documents = await _build_system_documents(db)

    sources = {document.source for document in documents}
    assert sources == {"product", "guide", "policy", "faq"}
    product_doc = next(document for document in documents if document.source == "product")
    assert product_doc.meta["product_id"] == 1
    assert "测试耳机" in product_doc.content
    assert "颜色：黑色" in product_doc.content


@pytest.mark.asyncio
async def test_index_documents_writes_chunks_with_embeddings(monkeypatch):
    monkeypatch.setattr(indexer_module, "get_embeddings", lambda: _FakeEmbeddings())

    async def fake_available(_db):
        return False

    monkeypatch.setattr(indexer_module, "pgvector_available", fake_available)

    documents = [
        RagDocument(id="d1", title="文档一", content="第一段内容。", category="通用"),
        RagDocument(id="d2", title="文档二", content="第二段内容。", category="通用"),
    ]
    db = _FakeDB()

    count = await _index_documents(db, documents)

    assert count == 2
    assert len(db.added) == 2
    assert db.added[0].embedding == [0.1, 0.2]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_index_documents_degrades_when_embedding_fails(monkeypatch):
    monkeypatch.setattr(
        indexer_module,
        "get_embeddings",
        lambda: _FakeEmbeddings(error=RuntimeError("model down")),
    )

    async def fake_available(_db):
        return False

    monkeypatch.setattr(indexer_module, "pgvector_available", fake_available)

    db = _FakeDB()
    count = await _index_documents(
        db, [RagDocument(id="d1", title="文档", content="内容。", category="通用")]
    )

    assert count == 1
    assert db.added[0].embedding == []


@pytest.mark.asyncio
async def test_index_documents_returns_zero_without_chunks(monkeypatch):
    async def fake_available(_db):
        return False

    monkeypatch.setattr(indexer_module, "pgvector_available", fake_available)

    assert await _index_documents(_FakeDB(), []) == 0


@pytest.mark.asyncio
async def test_write_vectors_skips_without_pgvector(monkeypatch):
    async def fake_available(_db):
        return False

    monkeypatch.setattr(indexer_module, "pgvector_available", fake_available)
    db = _FakeDB()

    await _write_vectors(db, [(SimpleNamespace(id="c1"), [0.1, 0.2])])

    assert db.executed == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_write_vectors_skips_empty_vectors(monkeypatch):
    async def fake_available(_db):
        return True

    monkeypatch.setattr(indexer_module, "pgvector_available", fake_available)
    db = _FakeDB()

    await _write_vectors(db, [(SimpleNamespace(id="c1"), [])])

    assert db.executed == []


@pytest.mark.asyncio
async def test_write_vectors_commits_on_success(monkeypatch):
    async def fake_available(_db):
        return True

    monkeypatch.setattr(indexer_module, "pgvector_available", fake_available)
    db = _FakeDB()

    await _write_vectors(
        db,
        [
            (SimpleNamespace(id="c1"), [0.1, 0.2]),
            (SimpleNamespace(id="c2"), [0.3, 0.4]),
        ],
    )

    assert len(db.executed) == 1
    assert db.commits == 1


@pytest.mark.asyncio
async def test_write_vectors_rolls_back_on_error(monkeypatch):
    async def fake_available(_db):
        return True

    monkeypatch.setattr(indexer_module, "pgvector_available", fake_available)

    class _BrokenDB(_FakeDB):
        async def execute(self, stmt, params=None):
            raise RuntimeError("vector write failed")

    db = _BrokenDB()

    await _write_vectors(db, [(SimpleNamespace(id="c1"), [0.1, 0.2])])

    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_rebuild_index_updates_existing_and_drops_stale(monkeypatch):
    monkeypatch.setattr(indexer_module, "get_embeddings", lambda: _FakeEmbeddings())

    async def fake_available(_db):
        return False

    monkeypatch.setattr(indexer_module, "pgvector_available", fake_available)

    product_id = 1
    existing_id = _system_id("product", product_id)
    existing = RagDocument(
        id=existing_id,
        title="旧标题",
        content="旧内容",
        category="手机数码",
        source="product",
    )
    stale = RagDocument(id="stale", title="过期", content="x", category="通用", source="product")

    db = _FakeDB(
        products=[_product(product_id)],
        faqs=[],
        documents=[existing, stale],
        rowcount=1,
    )

    stats = await rebuild_index(db)

    assert stats["documents"] >= 3  # 商品 + 分类指南 + 售后政策
    assert stats["chunks"] >= stats["documents"]
    assert existing.title == "测试耳机"
    assert db.executed  # 过期文档与旧切片被删除


@pytest.mark.asyncio
async def test_index_single_document_replaces_chunks(monkeypatch):
    monkeypatch.setattr(
        indexer_module, "get_embeddings", lambda: _FakeEmbeddings(vectors=[[0.5]])
    )

    async def fake_available(_db):
        return False

    monkeypatch.setattr(indexer_module, "pgvector_available", fake_available)
    db = _FakeDB()

    count = await index_single_document(
        db, RagDocument(id="d1", title="文档", content="内容。", category="通用")
    )

    assert count == 1
    assert db.executed and "rag_chunks" in db.executed[0][0]


def test_select_product_entity_is_detected_by_fake_db():
    """保证 _FakeDB 的实体分发与真实 select 行为一致。"""
    assert select(Product).column_descriptions[0]["entity"] is Product
