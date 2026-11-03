"""双路召回与 RRF 融合测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ecommerce_cs.rag import retriever as retriever_module
from ecommerce_cs.rag.retriever import (
    KnowledgeHit,
    _keyword_score,
    _numpy_search,
    _pgvector_search,
    _vector_search,
    extract_terms,
    format_hits,
    pgvector_available,
    reset_vector_capability,
    rrf_fuse,
    search,
    vector_literal,
)


def _hit(document_id: str, source: str, score: float = 0.1) -> KnowledgeHit:
    return KnowledgeHit(
        document_id=document_id,
        title=f"doc-{document_id}",
        doc_type="custom",
        category="test",
        content="content",
        score=score,
        sources=(source,),
    )


def _document(document_id: str, title: str, content: str, updated_at=None):
    return SimpleNamespace(
        id=document_id,
        title=title,
        content=content,
        doc_type="faq",
        category="test",
        updated_at=updated_at or datetime(2026, 1, 1, tzinfo=UTC),
    )


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, *, scalar_rows=(), execute_rows=(), scalar_values=()):
        self.scalar_rows = list(scalar_rows)
        self.execute_rows = list(execute_rows)
        self.scalar_values = list(scalar_values)

    async def scalars(self, _stmt):
        return _Rows(self.scalar_rows)

    async def execute(self, _stmt, _params=None):
        return _Rows(self.execute_rows)

    async def scalar(self, _stmt):
        return self.scalar_values.pop(0)


class _FakeEmbeddings:
    def __init__(self, vector=None, error: Exception | None = None):
        self.vector = vector or [1.0, 0.0]
        self.error = error

    async def embed_query(self, _query):
        if self.error:
            raise self.error
        return self.vector


def test_extract_terms_keeps_chinese_bigrams_and_drops_stopwords():
    terms = extract_terms("降噪耳机怎么选")
    assert "降噪" in terms
    assert "耳机" in terms
    assert "怎么" not in terms


def test_extract_terms_handles_english_and_empty_query():
    assert "iphone" in extract_terms("iPhone 15 值得买吗")
    assert extract_terms("") == []


def test_keyword_score_rewards_title_and_content_matches():
    document = _document("d1", "降噪耳机", "主动降噪，续航 30 小时")
    assert _keyword_score(document, "降噪", ["降噪"]) == 9.5  # 标题全文 +6、标题词 +2.5、正文词 +1
    assert _keyword_score(document, "蓝牙", ["蓝牙"]) == 0.0


def test_rrf_prefers_document_hit_by_both_paths():
    vector_hits = [_hit("a", "vector"), _hit("b", "vector")]
    keyword_hits = [_hit("b", "keyword"), _hit("c", "keyword")]

    hits = rrf_fuse(vector_hits, keyword_hits, limit=3)

    assert hits[0].document_id == "b"
    assert set(hits[0].sources) == {"vector", "keyword"}
    assert len(hits) == 3


def test_rrf_supports_single_path():
    hits = rrf_fuse([_hit("a", "vector")], [], limit=2)
    assert len(hits) == 1
    assert hits[0].document_id == "a"
    assert hits[0].sources == ("vector",)


def test_rrf_weights_can_promote_keyword_hit():
    vector_hits = [_hit("a", "vector"), _hit("b", "vector")]
    keyword_hits = [_hit("b", "keyword")]
    hits = rrf_fuse(
        vector_hits,
        keyword_hits,
        limit=2,
        k=10,
        weights={"vector": 1.0, "keyword": 3.0},
    )
    assert hits[0].document_id == "b"


def test_vector_literal_is_pgvector_compatible():
    literal = vector_literal([0.1, -0.25, 0.333333])
    assert literal.startswith("[") and literal.endswith("]")
    assert literal.count(",") == 2


def test_format_hits_with_and_without_results():
    assert format_hits([]) == "知识库中暂未找到相关内容。"
    body = format_hits([_hit("a", "vector")])
    assert body.startswith("[1] doc-a")
    assert "content" in body


def test_knowledge_hit_to_dict_rounds_score():
    payload = _hit("a", "vector", score=0.123456).to_dict()
    assert payload["score"] == 0.1235
    assert payload["sources"] == ["vector"]


@pytest.fixture(autouse=True)
def _reset_capability():
    reset_vector_capability()
    yield
    reset_vector_capability()


@pytest.mark.asyncio
async def test_pgvector_available_respects_explicit_backend(monkeypatch):
    monkeypatch.setattr(
        retriever_module,
        "get_settings",
        lambda: SimpleNamespace(rag_vector_backend="numpy"),
    )
    assert await pgvector_available(_FakeDB()) is False

    monkeypatch.setattr(
        retriever_module,
        "get_settings",
        lambda: SimpleNamespace(rag_vector_backend="pgvector"),
    )
    assert await pgvector_available(_FakeDB()) is True


@pytest.mark.asyncio
async def test_pgvector_available_probes_schema_once(monkeypatch):
    monkeypatch.setattr(
        retriever_module,
        "get_settings",
        lambda: SimpleNamespace(rag_vector_backend="auto"),
    )
    db = _FakeDB(scalar_values=[1, 1])
    assert await pgvector_available(db) is True

    # 结果缓存：第二次不再查询数据库
    db2 = _FakeDB(scalar_values=[])
    assert await pgvector_available(db2) is True


@pytest.mark.asyncio
async def test_pgvector_available_falls_back_on_probe_error(monkeypatch):
    class _BrokenDB(_FakeDB):
        async def scalar(self, _stmt):
            raise RuntimeError("no pg")

    monkeypatch.setattr(
        retriever_module,
        "get_settings",
        lambda: SimpleNamespace(rag_vector_backend="auto"),
    )

    assert await pgvector_available(_BrokenDB()) is False


@pytest.mark.asyncio
async def test_numpy_search_orders_by_cosine_and_dedupes_documents(monkeypatch):
    chunks = [
        SimpleNamespace(document_id="d1", content="片段 A", embedding=[1.0, 0.0]),
        SimpleNamespace(document_id="d1", content="片段 B", embedding=[0.9, 0.1]),
        SimpleNamespace(document_id="d2", content="片段 C", embedding=[0.0, 1.0]),
    ]
    documents = [
        _document("d1", "文档一", "内容一"),
        _document("d2", "文档二", "内容二"),
    ]

    async def fake_list_chunks(_db):
        return chunks

    monkeypatch.setattr(retriever_module.knowledge_repo, "list_chunks", fake_list_chunks)
    monkeypatch.setattr(
        retriever_module,
        "get_embeddings",
        lambda: _FakeEmbeddings(vector=[1.0, 0.0]),
    )

    hits = await _numpy_search(_FakeDB(scalar_rows=documents), "测试", limit=2)

    assert [hit.document_id for hit in hits] == ["d1", "d2"]
    assert hits[0].content == "片段 A"


@pytest.mark.asyncio
async def test_numpy_search_returns_empty_without_embeddings(monkeypatch):
    async def fake_list_chunks(_db):
        return [SimpleNamespace(document_id="d1", content="x", embedding=[])]

    monkeypatch.setattr(retriever_module.knowledge_repo, "list_chunks", fake_list_chunks)
    assert await _numpy_search(_FakeDB(), "测试", 2) == []


@pytest.mark.asyncio
async def test_numpy_search_returns_empty_when_embedding_call_fails(monkeypatch):
    async def fake_list_chunks(_db):
        return [SimpleNamespace(document_id="d1", content="x", embedding=[1.0])]

    monkeypatch.setattr(retriever_module.knowledge_repo, "list_chunks", fake_list_chunks)
    monkeypatch.setattr(
        retriever_module,
        "get_embeddings",
        lambda: _FakeEmbeddings(error=RuntimeError("model down")),
    )

    assert await _numpy_search(_FakeDB(), "测试", 2) == []


@pytest.mark.asyncio
async def test_pgvector_search_dedupes_chunk_rows(monkeypatch):
    rows = [
        SimpleNamespace(id="c1", document_id="d1", content="片段 A", score=0.9),
        SimpleNamespace(id="c2", document_id="d1", content="片段 B", score=0.8),
        SimpleNamespace(id="c3", document_id="d2", content="片段 C", score=0.7),
    ]
    documents = [
        _document("d1", "文档一", "内容一"),
        _document("d2", "文档二", "内容二"),
    ]

    monkeypatch.setattr(
        retriever_module,
        "get_embeddings",
        lambda: _FakeEmbeddings(vector=[1.0, 0.0]),
    )
    hits = await _pgvector_search(
        _FakeDB(scalar_rows=documents, execute_rows=rows), "测试", limit=2
    )

    assert [hit.document_id for hit in hits] == ["d1", "d2"]
    assert hits[0].sources == ("vector",)


@pytest.mark.asyncio
async def test_pgvector_search_returns_empty_when_embedding_fails(monkeypatch):
    monkeypatch.setattr(
        retriever_module,
        "get_embeddings",
        lambda: _FakeEmbeddings(error=RuntimeError("model down")),
    )
    assert await _pgvector_search(_FakeDB(), "测试", limit=2) == []


@pytest.mark.asyncio
async def test_vector_search_falls_back_to_numpy_on_pgvector_error(monkeypatch):
    async def fake_available(_db):
        return True

    async def broken_pg(_db, _query, _limit):
        raise RuntimeError("pgvector broken")

    async def fake_numpy(_db, _query, _limit):
        return [_hit("numpy", "vector")]

    monkeypatch.setattr(retriever_module, "pgvector_available", fake_available)
    monkeypatch.setattr(retriever_module, "_pgvector_search", broken_pg)
    monkeypatch.setattr(retriever_module, "_numpy_search", fake_numpy)

    hits = await _vector_search(_FakeDB(), "测试", 3)

    assert [hit.document_id for hit in hits] == ["numpy"]
    # 失败后 capability 标记为 False，避免反复尝试
    assert await pgvector_available(_FakeDB()) is False


@pytest.mark.asyncio
async def test_vector_search_uses_numpy_when_pgvector_returns_empty(monkeypatch):
    async def fake_available(_db):
        return True

    async def empty_pg(_db, _query, _limit):
        return []

    async def fake_numpy(_db, _query, _limit):
        return [_hit("numpy", "vector")]

    monkeypatch.setattr(retriever_module, "pgvector_available", fake_available)
    monkeypatch.setattr(retriever_module, "_pgvector_search", empty_pg)
    monkeypatch.setattr(retriever_module, "_numpy_search", fake_numpy)

    assert [hit.document_id for hit in await _vector_search(_FakeDB(), "x", 3)] == ["numpy"]


@pytest.mark.asyncio
async def test_search_short_circuits_empty_query():
    assert await search(_FakeDB(), "   ") == []


@pytest.mark.asyncio
async def test_search_fuses_paths_and_logs(monkeypatch):
    async def fake_vector(_db, _query, _limit):
        return [_hit("a", "vector"), _hit("b", "vector")]

    async def fake_keyword(_db, _query, _limit):
        return [_hit("b", "keyword")]

    logged = []

    async def fake_log(_db, query, top_score, hits):
        logged.append((query, top_score, hits))

    monkeypatch.setattr(retriever_module, "_vector_search", fake_vector)
    monkeypatch.setattr(retriever_module, "_keyword_search", fake_keyword)
    monkeypatch.setattr(retriever_module.knowledge_repo, "log_retrieval", fake_log)

    hits = await search(_FakeDB(), "降噪耳机", limit=2)

    assert hits[0].document_id == "b"
    assert logged and logged[0][0] == "降噪耳机"


@pytest.mark.asyncio
async def test_search_swallows_recall_failures(monkeypatch):
    async def broken(_db, _query, _limit):
        raise RuntimeError("recall down")

    async def fake_log(_db, query, top_score, hits):
        return None

    monkeypatch.setattr(retriever_module, "_vector_search", broken)
    monkeypatch.setattr(retriever_module, "_keyword_search", broken)
    monkeypatch.setattr(retriever_module.knowledge_repo, "log_retrieval", fake_log)

    assert await search(_FakeDB(), "退款", with_log=False) == []
