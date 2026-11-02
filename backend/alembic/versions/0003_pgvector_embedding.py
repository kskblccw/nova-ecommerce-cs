"""add pgvector embedding column and HNSW index (optional)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01

在支持 pgvector 的环境（Docker Compose 默认镜像）中启用向量列与 HNSW 索引；
Windows 本地 PostgreSQL 若未安装扩展则自动跳过，检索继续使用 JSONB + numpy 降级路径。
迁移本身不嵌入数据，升级后需执行一次 `python -m ecommerce_cs.rag.indexer` 回填。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            BEGIN
                CREATE EXTENSION IF NOT EXISTS vector;
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'pgvector unavailable, skip: %', SQLERRM;
            END;

            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                EXECUTE 'ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS embedding_vec vector(1024)';
                EXECUTE 'CREATE INDEX IF NOT EXISTS ix_rag_chunks_embedding_hnsw '
                        'ON rag_chunks USING hnsw (embedding_vec vector_cosine_ops)';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_embedding_hnsw")
    op.execute("ALTER TABLE rag_chunks DROP COLUMN IF EXISTS embedding_vec")
