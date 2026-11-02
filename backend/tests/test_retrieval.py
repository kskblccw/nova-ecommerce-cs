"""双路召回与 RRF 融合测试。"""

from ecommerce_cs.rag.retriever import (
    KnowledgeHit,
    extract_terms,
    rrf_fuse,
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


def test_extract_terms_keeps_chinese_bigrams_and_drops_stopwords():
    terms = extract_terms("降噪耳机怎么选")
    assert "降噪" in terms
    assert "耳机" in terms
    assert "怎么" not in terms


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
