"""知识库管理后台（管理员）。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.api.deps import get_admin_user
from ecommerce_cs.core.errors import ForbiddenError, NotFoundError
from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.db.models import User
from ecommerce_cs.db.repositories import knowledge as knowledge_repo
from ecommerce_cs.db.repositories import ops as ops_repo
from ecommerce_cs.db.session import get_db
from ecommerce_cs.rag.indexer import SYSTEM_SOURCES, index_single_document, rebuild_index
from ecommerce_cs.rag.retriever import search
from ecommerce_cs.schemas.knowledge import (
    DocumentCreate,
    DocumentList,
    DocumentOut,
    DocumentUpdate,
    KnowledgeStats,
    SearchRequest,
)

log = get_logger("admin")
router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/documents", response_model=DocumentList)
async def list_documents(
    category: str = Query(default=""),
    doc_type: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentList:
    documents, total = await knowledge_repo.list_documents(
        db, category=category, doc_type=doc_type, page=page, page_size=page_size
    )
    return DocumentList(
        total=total,
        page=page,
        page_size=page_size,
        items=[DocumentOut.model_validate(document) for document in documents],
    )


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: str,
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    document = await knowledge_repo.get_document(db, document_id)
    if not document:
        raise NotFoundError("文档不存在")
    return DocumentOut.model_validate(document)


async def _reindex(db: AsyncSession, document) -> None:
    try:
        await index_single_document(db, document)
    except Exception:
        log.exception("document_index_failed", document_id=document.id)


def _ensure_editable(document) -> None:
    if document.source in SYSTEM_SOURCES:
        raise ForbiddenError("系统文档由索引自动维护，请在知识库重建时同步更新")


async def _ensure_ids_editable(db: AsyncSession, document_ids: list[str]) -> None:
    for document_id in document_ids:
        document = await knowledge_repo.get_document(db, document_id)
        if document:
            _ensure_editable(document)


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def create_document(
    payload: DocumentCreate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    document = await knowledge_repo.create_document(
        db,
        title=payload.title,
        category=payload.category,
        doc_type=payload.doc_type,
        content=payload.content,
    )
    await _reindex(db, document)
    await ops_repo.audit(
        db,
        user_id=admin.id,
        action="knowledge.created",
        resource_type="rag_document",
        resource_id=document.id,
    )
    return DocumentOut.model_validate(document)


@router.put("/documents/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: str,
    payload: DocumentUpdate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    document = await knowledge_repo.get_document(db, document_id)
    if not document:
        raise NotFoundError("文档不存在")
    _ensure_editable(document)
    document = await knowledge_repo.update_document(
        db,
        document,
        title=payload.title,
        category=payload.category,
        doc_type=payload.doc_type,
        content=payload.content,
    )
    await _reindex(db, document)
    await ops_repo.audit(
        db,
        user_id=admin.id,
        action="knowledge.updated",
        resource_type="rag_document",
        resource_id=document.id,
    )
    return DocumentOut.model_validate(document)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    document = await knowledge_repo.get_document(db, document_id)
    if not document:
        raise NotFoundError("文档不存在")
    _ensure_editable(document)
    await knowledge_repo.delete_documents(db, [document_id])
    await ops_repo.audit(
        db,
        user_id=admin.id,
        action="knowledge.deleted",
        resource_type="rag_document",
        resource_id=document_id,
    )
    return {"deleted": document_id}


@router.delete("/documents")
async def batch_delete(
    ids: str = Query(..., description="逗号分隔的文档 ID"),
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    id_list = [item.strip() for item in ids.split(",") if item.strip()]
    await _ensure_ids_editable(db, id_list)
    deleted = await knowledge_repo.delete_documents(db, id_list)
    await ops_repo.audit(
        db,
        user_id=admin.id,
        action="knowledge.batch_deleted",
        resource_type="rag_document",
        payload={"ids": id_list, "count": deleted},
    )
    return {"deleted": deleted}


@router.post("/search")
async def search_documents(
    payload: SearchRequest,
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    hits = await search(db, payload.query, limit=payload.top_k)
    return {"query": payload.query, "results": [hit.to_dict() for hit in hits]}


@router.post("/reindex")
async def reindex(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stats = await rebuild_index(db)
    await ops_repo.audit(
        db,
        user_id=admin.id,
        action="knowledge.reindexed",
        payload=stats,
    )
    return {"message": "知识库重建完成", **stats}


@router.get("/stats", response_model=KnowledgeStats)
async def stats(
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeStats:
    total, doc_types, categories = await knowledge_repo.knowledge_stats(db)
    retrieval = await knowledge_repo.retrieval_summary(db)
    return KnowledgeStats(
        total=total,
        doc_types=doc_types,
        categories=categories,
        retrieval=retrieval,
    )


@router.get("/audit")
async def audit_log(
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    events = await ops_repo.list_audit_events(db, limit=limit)
    return [
        {
            "id": event.id,
            "user_id": event.user_id,
            "action": event.action,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        }
        for event in events
    ]
