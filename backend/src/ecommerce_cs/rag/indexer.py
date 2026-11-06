"""知识库索引：系统文档构建、切片、向量化与重建。"""

import asyncio
import uuid

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.db.models import FAQ, Product, RagChunk, RagDocument
from ecommerce_cs.db.session import SessionLocal
from ecommerce_cs.llm.factory import get_embeddings
from ecommerce_cs.rag import milvus_store
from ecommerce_cs.rag.chunking import split_text
from ecommerce_cs.rag.content import AFTERSALE_POLICY, CATEGORY_GUIDES, PRODUCT_TEMPLATES
from ecommerce_cs.rag.retriever import pgvector_available, vector_literal

SYSTEM_SOURCES = ("product", "guide", "policy", "faq")

log = get_logger("indexer")


def _system_id(source: str, key: object) -> str:
    """稳定 ID：重建知识库时同一来源文档保持不变，避免前端书签/引用失效。"""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"ecommerce_cs:rag:{source}:{key}").hex


def _format_specs(specs: dict | None) -> str:
    if not specs:
        return "暂无详细参数"
    return "；".join(f"{key}：{value}" for key, value in specs.items())


async def _build_system_documents(db: AsyncSession) -> list[RagDocument]:
    documents: list[RagDocument] = []

    products = (await db.scalars(select(Product))).all()
    for product in products:
        template = PRODUCT_TEMPLATES.get(product.category, PRODUCT_TEMPLATES["手机数码"])
        content = template.format(
            name=product.name,
            description=product.description or "",
            specs=_format_specs(product.specs),
        )
        documents.append(
            RagDocument(
                id=_system_id("product", product.id),
                title=product.name,
                category=product.category,
                doc_type="product_detail",
                content=content,
                source="product",
                meta={
                    "product_id": product.id,
                    "product_name": product.name,
                    "price": product.price,
                    "stock": product.stock,
                },
            )
        )

    for category, guide in CATEGORY_GUIDES.items():
        documents.append(
            RagDocument(
                id=_system_id("guide", category),
                title=f"{category}选购指南",
                category=category,
                doc_type="buying_guide",
                content=guide,
                source="guide",
            )
        )

    documents.append(
        RagDocument(
            id=_system_id("policy", "aftersale"),
            title="售后服务政策",
            category="售后政策",
            doc_type="policy",
            content=AFTERSALE_POLICY,
            source="policy",
        )
    )

    faqs = (await db.scalars(select(FAQ))).all()
    for faq in faqs:
        documents.append(
            RagDocument(
                id=_system_id("faq", faq.id),
                title=faq.question,
                category=faq.category,
                doc_type="faq",
                content=f"问：{faq.question}\n答：{faq.answer}",
                source="faq",
            )
        )

    return documents


async def _write_vectors(
    db: AsyncSession, chunks: list[tuple[RagChunk, list[float]]]
) -> None:
    """先写 Milvus 向量索引，再兼容写 pgvector；两者失败都只降级不阻塞。"""
    vectors = [(chunk, vector) for chunk, vector in chunks if vector]
    if not vectors:
        return

    milvus_store.upsert_chunks(
        [
            milvus_store.ChunkVector(
                chunk_id=str(chunk.id),
                document_id=str(getattr(chunk, "document_id", "")),
                chunk_index=int(getattr(chunk, "chunk_index", 0) or 0),
                embedding=vector,
            )
            for chunk, vector in vectors
        ]
    )

    if not await pgvector_available(db):
        return
    rows = [
        {"id": chunk.id, "vec": vector_literal(vector)}
        for chunk, vector in vectors
    ]
    try:
        await db.execute(
            text(
                "UPDATE rag_chunks SET embedding_vec = CAST(:vec AS vector) "
                "WHERE id = :id"
            ),
            rows,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        log.exception("pgvector_write_failed_fallback_to_jsonb")


async def _index_documents(db: AsyncSession, documents: list[RagDocument]) -> int:
    chunk_pairs: list[tuple[str, int, str]] = []
    for document in documents:
        for index, content in enumerate(split_text(document.content)):
            chunk_pairs.append((document.id, index, content))

    if not chunk_pairs:
        return 0

    try:
        embeddings = await get_embeddings().embed_documents([item[2] for item in chunk_pairs])
    except Exception:
        # 嵌入服务不可用时降级为纯关键词检索，不阻塞知识库写入
        log.exception("embedding_failed_fallback_to_keyword")
        embeddings = [[] for _ in chunk_pairs]

    if len(embeddings) != len(chunk_pairs):
        log.warning(
            "embedding_count_mismatch",
            expected=len(chunk_pairs),
            actual=len(embeddings),
        )

    chunks: list[tuple[RagChunk, list[float]]] = []
    for (document_id, index, content), vector in zip(
        chunk_pairs, embeddings, strict=False
    ):
        chunk = RagChunk(
            document_id=document_id,
            chunk_index=index,
            content=content,
            embedding=vector,
        )
        db.add(chunk)
        chunks.append((chunk, vector))
    await db.commit()

    await _write_vectors(db, chunks)
    return len(chunks)


async def rebuild_index(db: AsyncSession) -> dict[str, int]:
    """重建系统知识库（稳定 ID 原地更新，保留后台手工录入的自定义文档）。"""
    documents = await _build_system_documents(db)
    new_ids = {document.id for document in documents}

    existing = (
        await db.scalars(select(RagDocument).where(RagDocument.source.in_(SYSTEM_SOURCES)))
    ).all()
    existing_map = {document.id: document for document in existing}

    stale_ids = [document.id for document in existing if document.id not in new_ids]
    if stale_ids:
        await db.execute(delete(RagChunk).where(RagChunk.document_id.in_(stale_ids)))
        await db.execute(delete(RagDocument).where(RagDocument.id.in_(stale_ids)))
        await db.commit()
        milvus_store.delete_documents(stale_ids)

    for document in documents:
        current = existing_map.get(document.id)
        if current is None:
            db.add(document)
            continue
        current.title = document.title
        current.category = document.category
        current.doc_type = document.doc_type
        current.content = document.content
        current.source = document.source
        current.meta = document.meta
    await db.commit()

    await db.execute(delete(RagChunk).where(RagChunk.document_id.in_(new_ids)))
    await db.commit()
    milvus_store.delete_documents(list(new_ids))

    chunks = await _index_documents(db, documents)
    return {"documents": len(documents), "chunks": chunks}


async def index_single_document(db: AsyncSession, document: RagDocument) -> int:
    await db.execute(delete(RagChunk).where(RagChunk.document_id == document.id))
    await db.commit()
    milvus_store.delete_documents([document.id])
    return await _index_documents(db, [document])


async def _main() -> None:
    async with SessionLocal() as db:
        stats = await rebuild_index(db)
        print(f"知识库重建完成：{stats['documents']} 篇文档 / {stats['chunks']} 个切片")


if __name__ == "__main__":
    asyncio.run(_main())
