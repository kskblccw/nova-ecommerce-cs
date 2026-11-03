# Nova 电商智能客服

[![CI](https://github.com/kskblccw/nova-ecommerce-cs/actions/workflows/ci.yml/badge.svg)](https://github.com/kskblccw/nova-ecommerce-cs/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Tests](https://img.shields.io/badge/tests-199%20passed-brightgreen)

企业级多智能体电商客服系统：FastAPI + LangGraph 后端，Next.js 前端，
覆盖订单、物流、售后、商品导购、FAQ 与知识库检索。

> 架构与设计决策见 [docs/架构设计.md](docs/架构设计.md)。

## 在线演示

[打开交互演示 →](https://kskblccw.github.io/nova-ecommerce-cs/)

预置对话回放，展示确定性路由、工具调用、审批弹窗与回答链路；不连接线上数据库或大模型。
完整运行请使用下方 Docker 或本地启动方式。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12、FastAPI、LangGraph 1.x、LangChain、SQLAlchemy 2 async、Alembic |
| 前端 | Next.js 15 App Router、React 19、TypeScript、Tailwind CSS |
| 数据库 | PostgreSQL 16/18（业务数据 + LangGraph checkpoint），pgvector HNSW 向量索引（可选降级） |
| 限流 | Redis 滑动窗口（不可用时自动降级为进程内计数） |
| LLM | 阿里云 DashScope（qwen-plus）+ text-embedding-v3 |
| 可观测性 | structlog 结构化日志 + Prometheus 指标 + LangSmith 全链路追踪（可选） |
| 鉴权 | 手机号注册/登录，bcrypt 密码，JWT + httpOnly Cookie |

## 功能

- **手机号注册 / 登录 / 改密 / 登出**，会话与用户强绑定，客户端 phone 参数彻底废弃
- **确定性路由**：规则命中直接进域 Agent（零分类 LLM）；未命中只做一次结构化分类
- **多智能体**：订单、商品、售后、FAQ、人工转接五个域，工具权限由运行时上下文注入
- **结构化结果**：订单卡、物流时间线、商品卡、对比表、售后进度、FAQ 命中，前端按类型渲染
- **审批闭环**：变更类工具只产出 `PendingAction`，确认后才写库，`idempotency_key` 保证幂等
- **记忆系统**：会话摘要 + 用户画像，按 token 预算注入；后台异步压缩
- **知识库后台**：文档 CRUD、双路召回（pgvector HNSW / JSONB 向量 + 关键词加权 RRF）、命中统计、一键重建索引、审计日志
- **稳定性**：登录锁定/接口限流、LLM 熔断与降级回复、工具调用上限与超时、SSE 心跳、reset 撤回工具前旁白
- **一致性**：待确认操作期间禁止新消息；同一会话生成/审批串行化；客户端消息 ID 幂等，重试不重复落库
- **消息历史**：游标分页（`limit` + `before`），前端上滑加载更早消息
- **新功能**：取消订单、商品对比、缺货订阅、售后取消、人工工单、满意度反馈、会话重命名
- **工程化**：结构化日志 + request_id + 手机号脱敏、Alembic 单一 schema、单测/集成测试、
  ruff + mypy、Docker Compose

## 演示视频

[![演示视频](docs/demo-poster.png)](docs/demo.mp4)

95 秒走完主链路：登录 → 订单列表 → 物流轨迹 → 改地址二次确认 → 知识库后台。
视频使用演示数据（`seed_data --reset`）录制，未包含任何真实用户信息。

## 界面预览

| 登录 | 对话工作区 |
|---|---|
| ![登录](docs/screenshots/login.png) | ![对话工作区](docs/screenshots/chat.png) |
| **订单查询与结构化卡片** | **审批二次确认** |
| ![订单卡片](docs/screenshots/order-cards.png) | ![审批确认](docs/screenshots/approval.png) |
| **知识库后台** | |
| ![知识库后台](docs/screenshots/knowledge-admin.png) | |

## 效果数据

评测集在 `backend/benchmarks/retrieval_queries.jsonl`（55 条人工标注问法，覆盖商品、选购指南、FAQ、售后政策与型号类查询）：

| 检索策略 | Recall@4 | MRR@4 |
|---|---:|---:|
| 向量单路 | 98.2% | 0.955 |
| 关键词单路 | 83.6% | 0.750 |
| 加权 RRF（k=10，向量:关键词=2:1） | 98.2% | 0.945 |

检索链路压测（246 个切片、并发 8、排除远程 embedding 调用）：

| 向量实现 | QPS | P50 | P95 |
|---|---:|---:|---:|
| JSONB + numpy 全表余弦 | 7.5 | 902ms | 1038ms |
| pgvector + HNSW | 133.4 | 44ms | 62ms |

> 详细口径、逐条明细与复现命令见 [RAG 评测报告](docs/RAG评测报告.md) 与
> [RAG 压测报告](docs/RAG压测报告.md)。当前语料下向量单路已很强，RRF 主要作为
> 关键词降级与鲁棒性兜底；当知识库变为型号/专有名词密集型语料时应重新评测调权。

回答级端到端评测（`backend/benchmarks/golden_qa.jsonl`，25 条 × 3 轮，覆盖订单、售后、商品、FAQ、转人工与注入防护）：

| 指标 | 结果 |
|---|---:|
| 端到端通过率 | 75/75（100.0%） |
| 路由准确率 | 72/72（100.0%） |
| 工具调用匹配率 | 63/63（100.0%） |
| 事实要点覆盖率 | 60/60（100.0%） |
| 审批触发正确率 | 72/72（100.0%） |
| 越权/敏感表述 | 0 处 |

> 逐条明细、轮间稳定性与失败原因见 [回答质量评测报告](docs/回答质量评测报告.md)。
> 25 个用例全部 3/3 通过；平均 4.1s、P95 9.1s。

## 目录

```
new ecommerce_cs/
├─ backend/
│  ├─ src/ecommerce_cs/
│  │  ├─ core/          # 配置、日志、安全、错误
│  │  ├─ db/            # 模型、会话、仓储
│  │  ├─ llm/           # LLM / Embedding 工厂
│  │  ├─ rag/           # 内容、切分、索引、检索
│  │  ├─ agents/        # 状态、路由、工具、主图
│  │  ├─ services/      # 认证、对话、记忆、审批
│  │  ├─ api/routers/   # auth / conversations / chat / admin / health
│  │  └─ main.py
│  ├─ alembic/          # 迁移
│  ├─ benchmarks/       # RAG 评测集、评测与压测脚本
│  ├─ tests/            # 单元 + 集成测试
│  ├─ requirements.txt
│  └─ .env.example
├─ frontend/
│  ├─ src/app/          # login / chat / chat/[id] / admin/knowledge
│  ├─ src/components/   # 聊天、侧栏、结构化卡片、设计系统
│  └─ src/lib/          # API、SSE、类型、工具
├─ docs/架构设计.md
├─ docs/RAG评测报告.md
├─ docs/RAG压测报告.md
├─ docs/回答质量评测报告.md
├─ docs/demo.mp4
├─ docker-compose.yml
└─ README.md
```

## 本地运行

### 1. Python 环境

```bash
conda create -n ecommerce_cs python=3.12 -y
conda activate ecommerce_cs
pip install -r backend/requirements.txt        # 仅运行时
# 需要跑测试/静态检查时改用：
pip install -r backend/requirements-dev.txt
```

> 后端依赖已全部锁定到 `==` 精确版本，前端由 `package-lock.json` 锁定，CI 使用 `npm ci`。

### 2. 配置环境变量

复制模板并填写：

```bash
cp backend/.env.example backend/.env
```

关键变量：

```env
DATABASE_URL=postgresql+asyncpg://postgres:你的密码@localhost:5432/ecommerce_cs
DASHSCOPE_API_KEY=你的密钥
JWT_SECRET=随机长字符串
REDIS_URL=redis://localhost:6379/0
```

> `REDIS_URL` 为空时使用进程内限流；生产环境建议配置 Redis，并在 `APP_ENV=prod`
> 时通过启动校验（默认 JWT 密钥、非 TLS Cookie、默认数据库口令会直接拒绝启动）。

创建数据库（如尚未创建）：

```sql
CREATE DATABASE ecommerce_cs;
```

### 3. 初始化表与数据

```bash
cd backend
alembic upgrade head
python -m ecommerce_cs.seed_data --reset
# 可选：构建向量索引（需要 DASHSCOPE_API_KEY）
python -m ecommerce_cs.rag.indexer
```

> 迁移 `0003` 会自动检测 pgvector：扩展可用时创建 `embedding_vec vector(1024)` 与
> HNSW 索引；Windows 本地未安装扩展时自动跳过，检索继续使用 JSONB + numpy 降级路径。
> 启用 pgvector 后需执行一次 `python -m ecommerce_cs.rag.indexer` 回填向量。

> 若要一步完成：`python -m ecommerce_cs.seed_data --reset --with-index`

演示账号：

- 用户：`13800001001 / shop123456`（张伟，含待付款、已发货、已完成订单）
- 管理员：`13900000000 / shop123456`

### 4. 启动后端

Windows 本地推荐使用 `run.py`（自动加入 `src` 路径，并创建 psycopg 异步连接所需的 SelectorEventLoop）：

```powershell
cd backend
python run.py
```

Linux / macOS / 容器：

```bash
cd backend
python -m uvicorn ecommerce_cs.main:app --app-dir src --host 0.0.0.0 --port 8000
```

API 文档：http://localhost:8000/docs

### 5. 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:3000 。前端通过 Next.js rewrites 将 `/api/*` 代理到
`API_PROXY_TARGET`（默认 `http://127.0.0.1:8000`），因此 Cookie 始终同源。

> 排障：如果出现 `Cannot find module './xxx.js'`（`.next` 缓存损坏），执行
> `npm run dev:clean`。不要在 `next dev` 运行期间执行 `next build`，两者共用 `.next` 目录。

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 手机号注册 |
| POST | `/api/auth/login` | 登录（httpOnly Cookie） |
| GET | `/api/auth/me` | 当前用户 |
| POST | `/api/auth/logout` | 登出 |
| POST | `/api/auth/change-password` | 修改密码 |
| GET/POST | `/api/conversations` | 会话列表 / 新建 |
| PATCH/DELETE | `/api/conversations/{id}` | 重命名 / 删除 |
| GET | `/api/conversations/{id}/messages` | 消息历史（`limit` + `before` 游标分页） |
| POST | `/api/chat/{id}/stream` | SSE 流式对话 |
| GET | `/api/chat/{id}/pending` | 待确认操作 |
| POST | `/api/chat/{id}/approval` | 确认 / 取消 |
| POST | `/api/messages/{id}/feedback` | 满意度反馈 |
| GET/POST/PUT/DELETE | `/api/admin/documents` | 知识库 CRUD |
| POST | `/api/admin/search` | 语义检索 |
| POST | `/api/admin/reindex` | 重建索引 |
| GET | `/api/admin/stats` | 统计 |
| GET | `/api/admin/audit` | 审计日志 |
| GET | `/api/metrics` | Prometheus 指标 |
| GET | `/api/metrics/json` | 指标 JSON |

### SSE 事件

```
event: meta       {"conversation_id","title","run_id"}
event: status     {"stage","label"}
event: token      {"text"}
event: reset      {"reason"}          # 工具调用前旁白撤回
event: approval   {"action","action_id","summary","title"}
event: done       {"message_id","content","parts","degraded"}
event: error      {"code","message","degraded","retryable"}
```

内部节点（`agent`、`tools`、子图）不会进入事件流。超过 15 秒无输出时服务端会发送
`: ping` 注释行，避免反向代理按空闲超时断开连接。

## 安全设计

- 手机号只以 SHA-256 哈希存储与查找，展示为 `138****1001`，不进入 Prompt / 日志
- JWT 存 httpOnly Cookie；会话令牌在 DB 中以哈希保存，可撤销
- 所有会话/审批/反馈接口校验归属；管理员接口需 `role=admin`
- 变更操作二次确认；确认前不写业务库；审批采用原子 claim + 状态机 + 执行前二次归属校验，
  防并发/崩溃重复执行与越权
- Redis 分布式限流 + 登录失败锁定（手机号 + IP 双维度）
- 默认不信任 `X-Forwarded-For`，仅在可信反向代理后通过 `TRUST_FORWARDED_FOR=true` 开启
- 结构化日志统一脱敏手机号

## 可观测性

- **结构化日志**：structlog 输出 JSON，携带 `request_id`，手机号统一脱敏
- **运行指标**：`/api/metrics`（Prometheus 文本）与 `/api/metrics/json`，覆盖对话、降级、中断、取消、限流
- **链路追踪（LangSmith）**：开启后每次对话上报一条 trace，包含路由决策、每次工具调用的入参与结果、
  LLM 耗时与失败原因；`thread_id` 同时作为会话归组键，可按会话回放整段对话

开启方式（写入 `backend/.env`，或通过 compose 环境变量传入）：

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=nova-cs
# 可选，留空使用官方地址
LANGSMITH_ENDPOINT=
```

追踪在 `main.lifespan` 启动时由 `configure_tracing` 注入环境变量，业务代码无需改动；
未配置 API Key 时会自动关闭并记录一条 warning，不会给请求链路增加额外耗时。
本地已验证：开启后 LangSmith 项目 `nova-cs` 能正常收到每次对话的路由、工具与 LLM trace。

## 测试与评测

```bash
cd backend
pip install -r requirements-dev.txt   # 运行时 + 测试依赖一并安装
pytest -q                      # 单元测试
pytest -q -m integration       # 需要 PostgreSQL 的审批/SSE 集成测试
pytest -q --cov=src --cov-report=term-missing   # 覆盖率
ruff check src tests benchmarks
mypy src                       # 类型检查

cd ../frontend
npm run typecheck
npm test                       # SSE 解析与流式请求单测
npm run build
```

CI 在 `.github/workflows/ci.yml`：PostgreSQL 服务 + Alembic 迁移 + ruff + mypy +
单元/集成测试（附带覆盖率门槛）+ 前端 typecheck + 前端单测。

RAG 评测与压测：

```bash
cd backend
python benchmarks/eval_retrieval.py    # 生成 docs/RAG评测报告.md
python benchmarks/eval_agent.py        # 生成 docs/回答质量评测报告.md
python benchmarks/bench_retrieval.py --requests 300 --concurrency 8
```

## 已知限制

- **会话串行锁是进程内锁**：单实例部署可靠；多 worker / 多实例部署时需要替换为 Redis 分布式锁。
- **依赖锁定到直接依赖**：传递依赖仍由 pip 解析；需要完全可复现时可再引入 lock 文件。
- **前端测试聚焦纯逻辑**：已覆盖 SSE 解析与流式请求，组件交互仍以手工回归为主。
- **评测语料为演示数据**：246 个知识切片、55 条检索问法、25 条回答用例，不代表线上规模。

## Docker

```bash
docker compose up --build
```

- 前端：http://localhost:3000
- 后端：http://localhost:8000
- PostgreSQL（`pgvector/pgvector:pg16`，自带 vector 扩展）：localhost:5432
- Redis：localhost:6379

容器启动时会自动执行 `alembic upgrade head` 和幂等 seed（已有用户数据时自动跳过），
首次 `docker compose up --build` 即可直接使用。

如需重置演示数据，或关闭自动 seed：

```bash
docker compose exec api python -m ecommerce_cs.seed_data --reset --with-index
SEED_ON_START=false docker compose up --build
```
