# RAG 检索链路压测报告

- 生成时间：2026-11-01
- 压测对象：`ecommerce_cs.rag.retriever.search()`（向量召回 + 关键词召回 + RRF 融合）
- 数据集：55 条评测查询循环，共 300 次请求，并发 8，Top4
- 预热：20 次请求（不计入分位数）
- 说明：压测前预计算 query embedding，并在进程内用固定向量替换 embedding 客户端，
  因此下表**不含**外部 embedding API 延迟，只反映检索链路本身（SQL 召回 + 融合）。

## 环境

| 项 | numpy 基线 | Milvus Lite | pgvector |
|---|---|---|---|
| 向量实现 | JSONB 存向量 + numpy 全表余弦 | 本地 `milvus.db` + faiss 内核 | `vector(1024)` + HNSW 索引 |
| 数据库 | PostgreSQL 18.4（Windows 本地） | PostgreSQL 18.4（Windows 本地）+ Milvus Lite 3.2.2 | PostgreSQL 18.6 + pgvector 0.8.1（WSL） |
| 切片数 | 246 | 246 | 246 |
| 文档数 | 246 | 246 | 246 |
| 命中候选 | 两路各 12 条，RRF 后 Top4 | 同左 | 同左 |

> 本地验证使用 WSL 中的 PostgreSQL 18 + pgvector 0.8.1；项目默认部署使用
> `docker-compose.yml` 里的 `pgvector/pgvector:pg16` 镜像。

## 结果

| 后端 | QPS | P50 | P95 | P99 | 平均 | 最大 |
|---|---:|---:|---:|---:|---:|---:|
| JSONB + numpy 余弦 | 8.0 | 837.0ms | 970.5ms | 1378.3ms | 853.9ms | 1564.2ms |
| Milvus Lite（当前默认） | 45.3 | 142.7ms | 166.1ms | 598.2ms | 149.0ms | 745.9ms |
| pgvector + HNSW | 133.4 | 44.3ms | 61.8ms | 211.3ms | 49.1ms | 256.0ms |

- Milvus Lite 相对 numpy：吞吐 **5.7 倍**，P50 **5.9 倍**
- pgvector 仍是最快的单机后端；Milvus Lite 的价值在于向量库与业务库解耦，
  并能通过 `MILVUS_DB_URI=http://host:19530` 平滑切到独立集群

## 分析

1. numpy 路径每次请求都会把全部切片的 embedding 从 JSONB 读进内存，再在 Python 侧做矩阵乘法；
   246 条切片时单次就要数百毫秒，且随切片数线性增长。
2. pgvector 路径把相似度计算下沉到数据库，HNSW 索引只访问候选近邻，
   单次检索与总切片数基本解耦。
3. 两套环境的数据库主机不同（Windows 本地 vs WSL），pgvector 侧还多了 localhost 转发开销，
   但仍显著更快；因此差异主要来自检索算法，而不是主机性能。
4. 当前语料只有 246 条切片，HNSW 的收益尚未完全体现；切片规模到万级时，
   numpy 路径会接近不可用，pgvector 的优势会进一步扩大。
5. Milvus Lite 在进程内起 gRPC 服务，每次检索多一层本地 IPC，因此低于 pgvector；
   它的收益是零额外运维、向量库可独立扩展，而不是单机极限 QPS。

## 使用边界

- 这是检索链路微基准，不等价于线上容量：线上单次问答还包含约 400ms 的查询 embedding 调用、
  LLM 生成与工具执行。
- 并发 8、单机客户端，未做连接池与限流联调；生产容量需要按目标 QPS 单独压测。
- HNSW 参数使用默认 `m=16, ef_construction=64`，未针对召回率/延迟做调优。

## 复现

```bash
# 1) 准备带 pgvector 的数据库（二选一）
docker run -d --name cs-pgvector -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=ecommerce_cs_eval -p 5433:5432 pgvector/pgvector:pg16
# 或使用 docker compose 里的 pgvector/pgvector:pg16

cd backend
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/ecommerce_cs_eval \
  alembic upgrade head
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/ecommerce_cs_eval \
  python -m ecommerce_cs.seed_data --reset --with-index

# 2) numpy 基线（本地 PostgreSQL，未安装 pgvector）
python benchmarks/bench_retrieval.py --label numpy --requests 300 --concurrency 8

# 3) pgvector
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/ecommerce_cs_eval \
  python benchmarks/bench_retrieval.py --label pgvector --requests 300 --concurrency 8

# 4) Milvus Lite（默认后端，数据文件 backend/data/milvus.db）
python benchmarks/bench_retrieval.py --label milvus-lite --requests 300 --concurrency 8
```

原始结果：[bench_numpy.json](bench_numpy.json)、[bench_pgvector.json](bench_pgvector.json)。
