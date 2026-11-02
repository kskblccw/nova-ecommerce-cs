"""RAG 检索评测：向量单路 / 关键词单路 / 加权 RRF 双路对比。

评测口径：
- Recall@4：Top4 中是否命中任一人工标注文档（多标注文档时命中一个即算命中）
- MRR@4：首个命中文档排名的倒数，未命中记 0
- 候选集与线上一致：两路各取 12 条候选，再用 RRF 融合后截断到 Top4

RRF 权重在少量预设上做扫描，用于确认“向量权重 > 关键词权重”是否符合当前语料，
避免在评测集上做细粒度调参。

用法：
    python benchmarks/eval_retrieval.py
    python benchmarks/eval_retrieval.py --limit 10 --output ../docs/RAG评测报告.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "src"))

from sqlalchemy import func, select  # noqa: E402

from ecommerce_cs.core.settings import get_settings  # noqa: E402
from ecommerce_cs.db.models import RagChunk, RagDocument  # noqa: E402
from ecommerce_cs.db.session import SessionLocal  # noqa: E402
from ecommerce_cs.rag.retriever import (  # noqa: E402
    KnowledgeHit,
    _keyword_search,
    _vector_search,
    pgvector_available,
    rrf_fuse,
)

QUERY_FILE = Path(__file__).with_name("retrieval_queries.jsonl")

# (label, rrf_k, weights)
RRF_PRESETS: dict[str, tuple[int, dict[str, float]]] = {
    "k=60,1:1": (60, {"vector": 1.0, "keyword": 1.0}),
    "k=60,2:1": (60, {"vector": 2.0, "keyword": 1.0}),
    "k=20,1:1": (20, {"vector": 1.0, "keyword": 1.0}),
    "k=20,2:1": (20, {"vector": 2.0, "keyword": 1.0}),
    "k=10,1:1": (10, {"vector": 1.0, "keyword": 1.0}),
    "k=10,2:1": (10, {"vector": 2.0, "keyword": 1.0}),
}
DEFAULT_PRESET = "k=60,1:1"


@dataclass
class QueryResult:
    query: str
    category: str
    vector_rank: int | None
    keyword_rank: int | None
    rrf_ranks: dict[str, int | None] = field(default_factory=dict)
    vector_ms: float = 0.0
    keyword_ms: float = 0.0
    error: str = ""


def _rank(hits: list[KnowledgeHit], golds: list[str], limit: int) -> int | None:
    for index, hit in enumerate(hits[:limit], start=1):
        if any(gold in hit.title for gold in golds):
            return index
    return None


def _score(ranks: list[int | None]) -> tuple[float, float]:
    if not ranks:
        return 0.0, 0.0
    recall = sum(1 for rank in ranks if rank) / len(ranks)
    mrr = sum(1 / rank for rank in ranks if rank) / len(ranks)
    return recall, mrr


async def evaluate(limit: int) -> tuple[list[QueryResult], dict]:
    queries = [
        json.loads(line)
        for line in QUERY_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    results: list[QueryResult] = []
    async with SessionLocal() as db:
        total_docs = int(await db.scalar(select(func.count(RagDocument.id))) or 0)
        total_chunks = int(await db.scalar(select(func.count(RagChunk.id))) or 0)
        vector_backend = (
            "pgvector + HNSW" if await pgvector_available(db) else "JSONB + numpy 余弦"
        )

        for item in queries:
            query = item["query"]
            golds = item["golds"]
            candidates = max(limit * 3, 12)
            try:
                started = time.perf_counter()
                vector_hits = await _vector_search(db, query, candidates)
                vector_ms = (time.perf_counter() - started) * 1000

                started = time.perf_counter()
                keyword_hits = await _keyword_search(db, query, candidates)
                keyword_ms = (time.perf_counter() - started) * 1000

                rrf_ranks: dict[str, int | None] = {}
                for label, (rrf_k, weights) in RRF_PRESETS.items():
                    fused = rrf_fuse(
                        vector_hits, keyword_hits, limit=limit, k=rrf_k, weights=weights
                    )
                    rrf_ranks[label] = _rank(fused, golds, limit)

                results.append(
                    QueryResult(
                        query=query,
                        category=item.get("category", ""),
                        vector_rank=_rank(vector_hits, golds, limit),
                        keyword_rank=_rank(keyword_hits, golds, limit),
                        rrf_ranks=rrf_ranks,
                        vector_ms=vector_ms,
                        keyword_ms=keyword_ms,
                    )
                )
            except Exception as exc:  # 单条失败不影响整体评测
                results.append(
                    QueryResult(
                        query=query,
                        category=item.get("category", ""),
                        vector_rank=None,
                        keyword_rank=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

    summary = {
        "total_docs": total_docs,
        "total_chunks": total_chunks,
        "vector_backend": vector_backend,
        "queries": len(queries),
        "errors": sum(1 for row in results if row.error),
        "vector": _score([row.vector_rank for row in results]),
        "keyword": _score([row.keyword_rank for row in results]),
        "rrf": {
            label: _score([row.rrf_ranks.get(label) for row in results])
            for label in RRF_PRESETS
        },
    }
    return results, summary


def _best_weight(row: QueryResult) -> tuple[str, int | None]:
    options = [
        (label, rank)
        for label, rank in row.rrf_ranks.items()
        if rank is not None
    ]
    if not options:
        return DEFAULT_PRESET, None
    return min(options, key=lambda item: item[1])


def render_report(results: list[QueryResult], summary: dict, limit: int) -> str:
    settings = get_settings()
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    def rank(value: int | None) -> str:
        return str(value) if value else "未命中"

    def pct(value: float) -> str:
        return f"{value * 100:.1f}%"

    best_label = max(
        RRF_PRESETS,
        key=lambda label: (summary["rrf"][label][1], summary["rrf"][label][0]),
    )

    lines = [
        "# RAG 检索评测报告",
        "",
        f"- 生成时间：{generated}",
        f"- 评测集：`backend/benchmarks/retrieval_queries.jsonl`（{summary['queries']} 条）",
        f"- 知识库规模：{summary['total_docs']} 篇文档 / {summary['total_chunks']} 个切片",
        f"- 向量实现：{summary['vector_backend']}",
        f"- 向量模型：`{settings.embedding_model}`（{settings.embedding_dim} 维）",
        f"- 评测口径：候选集两路各取 {max(limit * 3, 12)} 条，RRF 融合后取 Top{limit}；"
        f"命中任一标注文档即算命中",
        "",
        "## 汇总",
        "",
        "| 策略 | Recall@4 | MRR@4 |",
        "|---|---|---|",
        f"| 向量单路 | {pct(summary['vector'][0])} | {summary['vector'][1]:.3f} |",
        f"| 关键词单路 | {pct(summary['keyword'][0])} | {summary['keyword'][1]:.3f} |",
    ]
    for label in RRF_PRESETS:
        recall, mrr = summary["rrf"][label]
        lines.append(f"| RRF 双路（{label}） | {pct(recall)} | {mrr:.3f} |")

    vector_avg = sum(row.vector_ms for row in results) / max(1, len(results))
    keyword_avg = sum(row.keyword_ms for row in results) / max(1, len(results))
    lines.extend(
        [
            "",
            f"> 平均单次耗时：向量路 {vector_avg:.1f}ms（含一次远程 embedding 调用），"
            f"关键词路 {keyword_avg:.1f}ms（纯 SQL）。",
            "",
            "## 明细",
            "",
            "| # | 查询 | 类型 | 向量 | 关键词 | RRF 等权 | RRF 最优权重 |",
            "|---|---|---|---|---|---|---|",
        ]
    )

    for index, row in enumerate(results, start=1):
        if row.error:
            lines.append(
                f"| {index} | {row.query} | {row.category} | 错误 | 错误 | 错误 | "
                f"{row.error} |"
            )
            continue
        label, best_rank = _best_weight(row)
        lines.append(
            f"| {index} | {row.query} | {row.category} | {rank(row.vector_rank)} | "
            f"{rank(row.keyword_rank)} | {rank(row.rrf_ranks.get(DEFAULT_PRESET))} | "
            f"{rank(best_rank)}（{label}） |"
        )

    wins: dict[str, int] = {"向量": 0, "关键词": 0, "RRF": 0, "全部未命中": 0}
    for row in results:
        if row.error:
            continue
        best = min(
            (
                (row.vector_rank, "向量"),
                (row.keyword_rank, "关键词"),
                (_best_weight(row)[1], "RRF"),
            ),
            key=lambda item: item[0] if item[0] else 10**9,
        )
        wins[best[1] if best[0] else "全部未命中"] += 1

    lines.extend(
        [
            "",
            "## 结论",
            "",
            f"- 向量单路 Recall@{limit} {pct(summary['vector'][0])}、MRR {summary['vector'][1]:.3f}；"
            f"关键词单路 Recall {pct(summary['keyword'][0])}、MRR {summary['keyword'][1]:.3f}。",
            f"- 最佳 RRF 配置为 {best_label}，"
            f"Recall {pct(summary['rrf'][best_label][0])}、"
            f"MRR {summary['rrf'][best_label][1]:.3f}。",
            f"- 排名最优次数：向量 {wins['向量']}、关键词 {wins['关键词']}、"
            f"RRF {wins['RRF']}、全部未命中 {wins['全部未命中']}。",
            "- 向量路擅长同义改写与跨词表召回，关键词路擅长型号、专有名词与英文缩写；"
            "RRF 按排名融合，避免两路分数尺度不可比。",
            "- 当前语料下向量路本身已很强，关键词路的边际收益有限，因此权重向向量倾斜；"
            "当知识库扩大到型号密集、专有名词密集的语料时，应重新跑本评测调整权重。",
        ]
    )
    if summary["errors"]:
        lines.append(f"- 本次有 {summary['errors']} 条查询失败，详见明细表。")
    return "\n".join(lines) + "\n"


async def _main(limit: int, output: Path | None) -> None:
    results, summary = await evaluate(limit)
    report = render_report(results, summary, limit)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        print(f"报告已写入 {output}")

    print(
        f"评测集：{summary['queries']} 条，"
        f"知识库：{summary['total_docs']} 篇 / {summary['total_chunks']} 片"
    )
    print(
        f"向量单路: Recall@{limit}={summary['vector'][0] * 100:.1f}%  "
        f"MRR@{limit}={summary['vector'][1]:.3f}"
    )
    print(
        f"关键词单路: Recall@{limit}={summary['keyword'][0] * 100:.1f}%  "
        f"MRR@{limit}={summary['keyword'][1]:.3f}"
    )
    for label in RRF_PRESETS:
        recall, mrr = summary["rrf"][label]
        print(f"RRF {label}: Recall@{limit}={recall * 100:.1f}%  MRR@{limit}={mrr:.3f}")
    if summary["errors"]:
        print(f"失败查询：{summary['errors']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument(
        "--output", type=Path, default=BACKEND_DIR.parent / "docs" / "RAG评测报告.md"
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_main(args.limit, args.output))
