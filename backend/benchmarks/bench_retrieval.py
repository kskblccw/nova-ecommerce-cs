"""RAG 检索链路压测：排除远程 embedding 调用，单独测 SQL 召回 + RRF 融合。

做法：先用评测集里的查询预计算一次 query embedding，再在压测期间用固定向量
替换 embedding 客户端，避免把外部 API 的延迟算进检索链路。

用法：
    python benchmarks/bench_retrieval.py --label "pgvector" --requests 400 --concurrency 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "src"))

from sqlalchemy import text  # noqa: E402

from ecommerce_cs.db.session import SessionLocal  # noqa: E402
from ecommerce_cs.llm.factory import get_embeddings  # noqa: E402
from ecommerce_cs.rag import milvus_store, retriever  # noqa: E402

QUERY_FILE = Path(__file__).with_name("retrieval_queries.jsonl")


def load_queries() -> list[str]:
    return [
        json.loads(line)["query"]
        for line in QUERY_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class FixedEmbeddings:
    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    async def embed_query(self, value: str) -> list[float]:
        return self._vectors[value]

    async def embed_documents(self, values: list[str]) -> list[list[float]]:
        return [self._vectors[value] for value in values]


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(len(ordered) * ratio)) - 1))
    return ordered[index]


async def backend_info() -> tuple[str, int]:
    async with SessionLocal() as db:
        mode = retriever.get_settings().rag_vector_backend
        available = await retriever.pgvector_available(db)
        try:
            count = int(
                (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM rag_chunks WHERE embedding_vec IS NOT NULL"
                        )
                    )
                ).scalar()
                or 0
            )
        except Exception:
            # 本地未安装 pgvector 时该列不存在，按 0 处理
            await db.rollback()
            count = 0
        chunks = int((await db.execute(text("SELECT count(*) FROM rag_chunks"))).scalar() or 0)
    milvus = milvus_store.available()
    if milvus:
        label = "Milvus Lite"
        count = milvus_store.count()
    else:
        label = "pgvector + HNSW" if available else "JSONB + numpy 余弦"
    return f"{label}（配置 {mode}，向量 {count}/{chunks}）", chunks


async def bench(requests: int, concurrency: int, limit: int) -> dict:
    queries = load_queries()
    embedder = get_embeddings()
    vectors: dict[str, list[float]] = {}
    for query in queries:
        vectors[query] = await embedder.embed_query(query)
    retriever.get_embeddings = lambda: FixedEmbeddings(vectors)  # type: ignore[assignment]

    async def one(query: str) -> float:
        async with SessionLocal() as db:
            started = time.perf_counter()
            await retriever.search(db, query, limit=limit, with_log=False)
            return (time.perf_counter() - started) * 1000

    # 预热，避免把连接建立与首查开销算进分位
    for index in range(min(20, len(queries))):
        await one(queries[index])

    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    started_all = time.perf_counter()

    async def worker(index: int) -> None:
        async with semaphore:
            latencies.append(await one(queries[index % len(queries)]))

    await asyncio.gather(*(worker(index) for index in range(requests)))
    elapsed = time.perf_counter() - started_all

    return {
        "requests": requests,
        "concurrency": concurrency,
        "elapsed_seconds": round(elapsed, 2),
        "qps": round(requests / elapsed, 1) if elapsed else 0.0,
        "p50_ms": round(percentile(latencies, 0.50), 2),
        "p95_ms": round(percentile(latencies, 0.95), 2),
        "p99_ms": round(percentile(latencies, 0.99), 2),
        "avg_ms": round(statistics.fmean(latencies), 2),
        "max_ms": round(max(latencies), 2),
    }


async def _main(args: argparse.Namespace) -> None:
    label, chunks = await backend_info()
    result = await bench(args.requests, args.concurrency, args.limit)
    output = {"label": args.label or label, "backend": label, "chunks": chunks, **result}

    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"结果已写入 {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--label", default="")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_main(args))
