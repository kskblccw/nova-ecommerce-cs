"""知识库文档、切片与检索日志的仓储。"""

from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.db.models import RagChunk, RagDocument, RetrievalLog


async def list_documents(
    db: AsyncSession,
    *,
    category: str = "",
    doc_type: str = "",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[RagDocument], int]:
    conditions = []
    if category:
        conditions.append(RagDocument.category == category)
    if doc_type:
        conditions.append(RagDocument.doc_type == doc_type)

    total = int(
        await db.scalar(
            select(func.count(RagDocument.id)).where(*conditions)
        )
        or 0
    )
    result = await db.scalars(
        select(RagDocument)
        .where(*conditions)
        .order_by(RagDocument.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.all()), total


async def get_document(db: AsyncSession, document_id: str) -> RagDocument | None:
    return await db.scalar(select(RagDocument).where(RagDocument.id == document_id))


async def create_document(
    db: AsyncSession,
    *,
    title: str,
    category: str,
    doc_type: str,
    content: str,
    source: str = "manual",
    meta: dict | None = None,
) -> RagDocument:
    document = RagDocument(
        title=title,
        category=category,
        doc_type=doc_type,
        content=content,
        source=source,
        meta=meta or {},
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


async def update_document(
    db: AsyncSession,
    document: RagDocument,
    *,
    title: str | None = None,
    category: str | None = None,
    doc_type: str | None = None,
    content: str | None = None,
) -> RagDocument:
    if title is not None:
        document.title = title
    if category is not None:
        document.category = category
    if doc_type is not None:
        document.doc_type = doc_type
    if content is not None:
        document.content = content
    document.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(document)
    return document


async def delete_documents(db: AsyncSession, document_ids: list[str]) -> int:
    if not document_ids:
        return 0
    result = await db.execute(delete(RagDocument).where(RagDocument.id.in_(document_ids)))
    await db.commit()
    return getattr(result, "rowcount", 0) or 0


async def delete_all_documents(db: AsyncSession) -> None:
    await db.execute(delete(RagDocument))
    await db.commit()


async def list_chunks(db: AsyncSession) -> list[RagChunk]:
    result = await db.scalars(select(RagChunk))
    return list(result.all())


async def replace_chunks(
    db: AsyncSession,
    document_id: str,
    chunks: list[tuple[str, list[float]]],
) -> None:
    await db.execute(delete(RagChunk).where(RagChunk.document_id == document_id))
    for index, (content, embedding) in enumerate(chunks):
        db.add(
            RagChunk(
                document_id=document_id,
                chunk_index=index,
                content=content,
                embedding=embedding,
            )
        )
    await db.commit()


async def knowledge_stats(db: AsyncSession) -> tuple[int, dict[str, int], dict[str, int]]:
    total = int(await db.scalar(select(func.count(RagDocument.id))) or 0)
    doc_type_rows = await db.execute(
        select(RagDocument.doc_type, func.count(RagDocument.id)).group_by(RagDocument.doc_type)
    )
    category_rows = await db.execute(
        select(RagDocument.category, func.count(RagDocument.id)).group_by(RagDocument.category)
    )
    return (
        total,
        {row[0]: int(row[1]) for row in doc_type_rows.all()},
        {row[0]: int(row[1]) for row in category_rows.all()},
    )


async def log_retrieval(db: AsyncSession, query: str, top_score: float, hits: int) -> None:
    db.add(RetrievalLog(query=query[:300], top_score=top_score, hits=hits))
    await db.commit()


async def retrieval_summary(db: AsyncSession) -> dict[str, float | int]:
    total = int(await db.scalar(select(func.count(RetrievalLog.id))) or 0)
    avg_score = float(await db.scalar(select(func.avg(RetrievalLog.top_score))) or 0)
    return {"queries": total, "avg_top_score": round(avg_score, 3)}
