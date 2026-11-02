"""双路召回 + RRF 融合检索。

路径一：向量召回（JSONB 中的 embedding + 余弦相似度）。
路径二：关键词召回（中文 bigram + 标题加权，直接走 SQL ILIKE）。
最后用 Reciprocal Rank Fusion 合并两路排名，避免单一召回策略的偏好。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.core.settings import get_settings
from ecommerce_cs.db.models import RagDocument
from ecommerce_cs.db.repositories import knowledge as knowledge_repo
from ecommerce_cs.llm.factory import get_embeddings

log = get_logger("retriever")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_RE = re.compile(r"[a-zA-Z0-9]{2,}")
_SPLIT_RE = re.compile(r"[\s,，。！？!?;；:：/|()（）\[\]【】\"'“”]+")

_STOP_TERMS = {
    "怎么",
    "么选",
    "什么",
    "可以",
    "是否",
    "如何",
    "哪些",
    "哪个",
    "多少",
    "我们",
    "你们",
    "请问",
    "帮我",
    "想要",
    "需要",
    "一个",
    "一下",
    "这个",
    "那个",
    "有没",
    "没有",
}


@dataclass
class KnowledgeHit:
    document_id: str
    title: str
    doc_type: str
    category: str
    content: str
    score: float
    sources: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "doc_type": self.doc_type,
            "category": self.category,
            "content": self.content,
            "score": round(self.score, 4),
            "sources": list(self.sources),
        }


def extract_terms(query: str) -> list[str]:
    """抽取用于关键词召回的中文词/二元组，过滤高频无意义词。"""
    settings = get_settings()
    terms: list[str] = []

    def add(term: str) -> None:
        term = term.strip().lower()
        if len(term) < 2 or term in _STOP_TERMS or term in terms:
            return
        terms.append(term)

    for segment in _SPLIT_RE.split((query or "").strip()):
        segment = segment.strip()
        if not segment:
            continue
        if 2 <= len(segment) <= 16:
            add(segment)
        for word in _ASCII_RE.findall(segment):
            add(word)
        for run in _CJK_RE.findall(segment):
            if 2 <= len(run) <= 8:
                add(run)
            for index in range(len(run) - 1):
                add(run[index : index + 2])

    terms.sort(key=len, reverse=True)
    return terms[: settings.rag_keyword_max_terms]


def _keyword_score(document: RagDocument, query: str, terms: list[str]) -> float:
    title = document.title or ""
    content = document.content or ""
    text = query.strip()
    score = 0.0
    if text:
        if text in title:
            score += 6.0
        elif text in content:
            score += 3.0
    for term in terms:
        if term in title:
            score += 2.5
        if term in content:
            score += 1.0
    return score


async def _keyword_search(
    db: AsyncSession, query: str, limit: int
) -> list[KnowledgeHit]:
    terms = extract_terms(query)
    if not terms:
        pattern = f"%{query[:60]}%"
        conditions = [or_(RagDocument.title.ilike(pattern), RagDocument.content.ilike(pattern))]
    else:
        conditions = [
            or_(RagDocument.title.ilike(f"%{term}%"), RagDocument.content.ilike(f"%{term}%"))
            for term in terms
        ]

    rows = (
        await db.scalars(
            select(RagDocument).where(or_(*conditions)).limit(max(limit * 3, 20))
        )
    ).all()
    scored = sorted(
        ((document, _keyword_score(document, query, terms)) for document in rows),
        key=lambda item: (item[1], item[0].updated_at),
        reverse=True,
    )
    hits: list[KnowledgeHit] = []
    for document, score in scored:
        if score <= 0:
            continue
        hits.append(
            KnowledgeHit(
                document_id=document.id,
                title=document.title,
                doc_type=document.doc_type,
                category=document.category,
                content=document.content[:400],
                score=float(score),
                sources=("keyword",),
            )
        )
        if len(hits) >= limit:
            break
    return hits


_VECTOR_CAPABILITY: bool | None = None


def vector_literal(vector: list[float]) -> str:
    """pgvector 参数文本：'[0.1,0.2,...]'，避免依赖驱动侧类型注册。"""
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


def reset_vector_capability() -> None:
    global _VECTOR_CAPABILITY
    _VECTOR_CAPABILITY = None


async def pgvector_available(db: AsyncSession) -> bool:
    """检测 pgvector 扩展与 embedding_vec 列是否就绪，结果按进程缓存。"""
    global _VECTOR_CAPABILITY
    mode = get_settings().rag_vector_backend.strip().lower()
    if mode == "numpy":
        return False
    if mode == "pgvector":
        return True if _VECTOR_CAPABILITY is None else _VECTOR_CAPABILITY
    if _VECTOR_CAPABILITY is not None:
        return _VECTOR_CAPABILITY
    try:
        has_column = await db.scalar(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'rag_chunks' AND column_name = 'embedding_vec'
                """
            )
        )
        has_extension = await db.scalar(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        _VECTOR_CAPABILITY = bool(has_column and has_extension)
    except Exception:
        log.exception("pgvector_detect_failed")
        _VECTOR_CAPABILITY = False
    return _VECTOR_CAPABILITY


async def _pgvector_search(
    db: AsyncSession, query: str, limit: int
) -> list[KnowledgeHit]:
    try:
        query_vector = await get_embeddings().embed_query(query)
    except Exception:
        log.exception("embedding_query_failed_fallback_keyword")
        return []
    if not query_vector:
        return []

    rows = (
        await db.execute(
            text(
                """
                SELECT id, document_id, content,
                       1 - (embedding_vec <=> CAST(:vec AS vector)) AS score
                FROM rag_chunks
                WHERE embedding_vec IS NOT NULL
                ORDER BY embedding_vec <=> CAST(:vec AS vector)
                LIMIT :limit
                """
            ),
            {"vec": vector_literal(query_vector), "limit": limit * 2},
        )
    ).all()
    if not rows:
        return []

    document_ids = {str(row.document_id) for row in rows}
    documents = {
        document.id: document
        for document in (
            await db.scalars(select(RagDocument).where(RagDocument.id.in_(document_ids)))
        ).all()
    }

    hits: list[KnowledgeHit] = []
    seen_documents: set[str] = set()
    for row in rows:
        document_id = str(row.document_id)
        if document_id in seen_documents:
            continue
        document = documents.get(document_id)
        if not document:
            continue
        seen_documents.add(document_id)
        hits.append(
            KnowledgeHit(
                document_id=document.id,
                title=document.title,
                doc_type=document.doc_type,
                category=document.category,
                content=row.content,
                score=float(row.score),
                sources=("vector",),
            )
        )
        if len(hits) >= limit:
            break
    return hits


async def _numpy_search(db: AsyncSession, query: str, limit: int) -> list[KnowledgeHit]:
    chunks = await knowledge_repo.list_chunks(db)
    valid_chunks = [chunk for chunk in chunks if chunk.embedding]
    if not valid_chunks:
        return []

    try:
        query_vector = np.array(await get_embeddings().embed_query(query), dtype=np.float32)
    except Exception:
        log.exception("embedding_query_failed_fallback_keyword")
        return []

    matrix = np.array([chunk.embedding for chunk in valid_chunks], dtype=np.float32)
    denominator = np.linalg.norm(query_vector) * np.linalg.norm(matrix, axis=1)
    denominator[denominator == 0] = 1e-9
    scores = matrix @ query_vector / denominator
    top_indices = np.argsort(scores)[::-1][: limit * 2]

    document_ids = {valid_chunks[int(index)].document_id for index in top_indices}
    documents = {
        document.id: document
        for document in (
            await db.scalars(select(RagDocument).where(RagDocument.id.in_(document_ids)))
        ).all()
    }

    hits: list[KnowledgeHit] = []
    seen_documents: set[str] = set()
    for index in top_indices:
        chunk = valid_chunks[int(index)]
        if chunk.document_id in seen_documents:
            continue
        document = documents.get(chunk.document_id)
        if not document:
            continue
        seen_documents.add(chunk.document_id)
        hits.append(
            KnowledgeHit(
                document_id=document.id,
                title=document.title,
                doc_type=document.doc_type,
                category=document.category,
                content=chunk.content,
                score=float(scores[int(index)]),
                sources=("vector",),
            )
        )
        if len(hits) >= limit:
            break
    return hits


async def _vector_search(
    db: AsyncSession, query: str, limit: int
) -> list[KnowledgeHit]:
    global _VECTOR_CAPABILITY
    if await pgvector_available(db):
        try:
            hits = await _pgvector_search(db, query, limit)
            if hits:
                return hits
        except Exception:
            log.exception("pgvector_search_failed_fallback_numpy")
            _VECTOR_CAPABILITY = False
    return await _numpy_search(db, query, limit)


def rrf_fuse(
    vector_hits: list[KnowledgeHit],
    keyword_hits: list[KnowledgeHit],
    *,
    limit: int,
    k: int | None = None,
    weights: dict[str, float] | None = None,
) -> list[KnowledgeHit]:
    """Weighted Reciprocal Rank Fusion：score = Σ weight / (k + rank)。"""
    settings = get_settings()
    rrf_k = k if k is not None else settings.rag_rrf_k
    source_weights = weights or {}
    fused_scores: dict[str, float] = {}
    best_hit: dict[str, KnowledgeHit] = {}
    sources: dict[str, set[str]] = {}

    for source, hits in (("vector", vector_hits), ("keyword", keyword_hits)):
        weight = float(source_weights.get(source, 1.0))
        for rank, hit in enumerate(hits, start=1):
            key = hit.document_id
            fused_scores[key] = fused_scores.get(key, 0.0) + weight / (rrf_k + rank)
            sources.setdefault(key, set()).add(source)
            current = best_hit.get(key)
            # 向量切片通常比文档前 400 字更相关，优先保留
            if current is None or source == "vector":
                best_hit[key] = hit

    ordered = sorted(fused_scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    merged: list[KnowledgeHit] = []
    for document_id, score in ordered:
        base = best_hit[document_id]
        merged.append(
            KnowledgeHit(
                document_id=document_id,
                title=base.title,
                doc_type=base.doc_type,
                category=base.category,
                content=base.content,
                score=score,
                sources=tuple(sorted(sources[document_id])),
            )
        )
    return merged


async def search(
    db: AsyncSession, query: str, *, limit: int = 4, with_log: bool = True
) -> list[KnowledgeHit]:
    query = (query or "").strip()
    if not query:
        return []

    settings = get_settings()
    try:
        vector_hits = await _vector_search(
            db, query, max(limit, settings.rag_vector_candidates)
        )
    except Exception:
        log.exception("vector_recall_failed")
        vector_hits = []
    try:
        keyword_hits = await _keyword_search(
            db, query, max(limit, settings.rag_keyword_candidates)
        )
    except Exception:
        log.exception("keyword_recall_failed")
        keyword_hits = []

    hits = rrf_fuse(
        vector_hits,
        keyword_hits,
        limit=limit,
        weights={
            "vector": settings.rag_vector_weight,
            "keyword": settings.rag_keyword_weight,
        },
    )

    if with_log:
        await knowledge_repo.log_retrieval(
            db, query, hits[0].score if hits else 0.0, len(hits)
        )
    return hits


def format_hits(hits: list[KnowledgeHit], *, max_chars: int = 350) -> str:
    if not hits:
        return "知识库中暂未找到相关内容。"
    lines = []
    for index, hit in enumerate(hits, 1):
        lines.append(
            f"[{index}] {hit.title}（{hit.category}）\n{hit.content[:max_chars].strip()}"
        )
    return "\n\n---\n\n".join(lines)
