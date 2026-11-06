"""Milvus Lite 向量索引。

职责边界：Milvus 只存切片向量与归属（chunk_id / document_id / chunk_index），
切片正文仍以 PostgreSQL 为准。检索命中后按 chunk_id 回查正文，
避免两套存储出现正文不一致。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.core.settings import get_settings

log = get_logger("milvus_store")

# Milvus Lite 是进程内嵌服务，同一数据文件只能有一个连接，
# 用可重入锁串行化初始化与写操作，避免并发重建时重复建集合。
_lock = threading.RLock()
_client = None
_available: bool | None = None

_DELETE_BATCH = 500


@dataclass(frozen=True)
class ChunkVector:
    chunk_id: str
    document_id: str
    chunk_index: int
    embedding: list[float]


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    document_id: str
    chunk_index: int
    score: float


def normalize_uri(uri: str) -> str:
    """本地文件统一成正斜杠：Milvus 会把 Windows 盘符误判为 database 名。"""
    value = (uri or "").strip()
    if not value or "://" in value or value.startswith("unix:"):
        return value
    return value.replace("\\", "/")


def _ensure_parent_dir(uri: str) -> None:
    if "://" in uri or uri.startswith("unix:"):
        return
    try:
        Path(uri).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.exception("milvus_data_dir_failed", uri=uri)


def _ensure_collection(client) -> None:
    from pymilvus import DataType, MilvusClient

    settings = get_settings()
    name = settings.milvus_collection
    if client.has_collection(name):
        return

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("document_id", DataType.VARCHAR, max_length=64)
    schema.add_field("chunk_index", DataType.INT64)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=settings.embedding_dim)

    index_params = client.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(name, schema=schema, index_params=index_params)
    log.info(
        "milvus_collection_created",
        collection=name,
        dim=settings.embedding_dim,
    )


def _ensure_loaded(client) -> None:
    """Milvus 重启后集合处于 released 状态，检索前必须显式 load。"""
    try:
        client.load_collection(get_settings().milvus_collection)
    except Exception:
        log.exception("milvus_load_failed")
        raise


def _get_client():
    global _client
    settings = get_settings()
    with _lock:
        if _client is not None:
            return _client
        from pymilvus import MilvusClient

        uri = normalize_uri(settings.milvus_db_uri)
        if not uri:
            raise RuntimeError("MILVUS_DB_URI 未配置")
        _ensure_parent_dir(uri)
        # db_name 必须显式指定，否则 pymilvus 会把路径首段当成 database 名
        _client = MilvusClient(uri, db_name="default")
        _ensure_collection(_client)
        _ensure_loaded(_client)
        log.info("milvus_connected", uri=uri, collection=settings.milvus_collection)
        return _client


def available() -> bool:
    """向量库是否可用；结果按进程缓存，失败自动降级到 pgvector / numpy。"""
    global _available

    mode = get_settings().rag_vector_backend.strip().lower()
    if mode in ("numpy", "pgvector"):
        return False

    with _lock:
        if _available is not None:
            return _available
        try:
            _get_client()
            _available = True
        except Exception:
            log.exception("milvus_init_failed_fallback")
            _available = False
        return _available


def mark_unavailable() -> None:
    global _available
    with _lock:
        _available = False


def disable() -> None:
    """强制按不可用处理（单元测试保持确定性，不落盘数据文件）。"""
    global _available
    with _lock:
        _available = False


def reset() -> None:
    """重置单例（测试与配置热更新使用）。"""
    global _client, _available
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                log.warning("milvus_close_failed")
        _client = None
        _available = None


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def upsert_chunks(rows: list[ChunkVector]) -> int:
    """按片写入向量；Milvus 不可用时静默返回 0，由上层继续走降级检索。"""
    valid = [row for row in rows if row.embedding]
    if not valid or not available():
        return 0

    client = _get_client()
    collection = get_settings().milvus_collection
    data = [
        {
            "id": row.chunk_id,
            "document_id": row.document_id,
            "chunk_index": row.chunk_index,
            "vector": row.embedding,
        }
        for row in valid
    ]
    try:
        client.upsert(collection, data)
    except Exception:
        log.exception("milvus_upsert_failed")
        mark_unavailable()
        return 0
    return len(data)


def delete_documents(document_ids: list[str]) -> int:
    # 保留输入顺序去重：过滤表达式稳定，便于排障与复现
    ids: list[str] = []
    for value in document_ids:
        item = value.strip()
        if item and item not in ids:
            ids.append(item)
    if not ids or not available():
        return 0

    client = _get_client()
    collection = get_settings().milvus_collection
    removed = 0
    for start in range(0, len(ids), _DELETE_BATCH):
        batch = ids[start : start + _DELETE_BATCH]
        expression = "document_id in [" + ", ".join(_quote(value) for value in batch) + "]"
        try:
            client.delete(collection, filter=expression)
            removed += len(batch)
        except Exception:
            log.exception("milvus_delete_failed", batch=len(batch))
            mark_unavailable()
            break
    return removed


def search(embedding: list[float], limit: int) -> list[VectorHit]:
    if not embedding or limit <= 0 or not available():
        return []

    client = _get_client()
    collection = get_settings().milvus_collection
    try:
        result = client.search(
            collection,
            data=[embedding],
            limit=limit,
            output_fields=["document_id", "chunk_index"],
            search_params={"metric_type": "COSINE"},
        )
    except Exception:
        log.exception("milvus_search_failed")
        mark_unavailable()
        return []

    hits: list[VectorHit] = []
    for item in result[0] if result else []:
        entity = item.get("entity") or {}
        document_id = entity.get("document_id")
        if not document_id:
            continue
        hits.append(
            VectorHit(
                chunk_id=str(item.get("id", "")),
                document_id=str(document_id),
                chunk_index=int(entity.get("chunk_index", 0) or 0),
                score=float(item.get("distance", 0.0)),
            )
        )
    return hits


def count() -> int:
    if not available():
        return 0
    try:
        stats = _get_client().get_collection_stats(get_settings().milvus_collection)
        return int(stats.get("row_count", 0) or 0)
    except Exception:
        log.exception("milvus_stats_failed")
        return 0
