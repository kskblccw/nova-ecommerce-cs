"""add client_message_id for request idempotency

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02

前端每次发送生成一个 client_message_id，重试时复用；唯一索引保证同一请求
不会在消息表里重复落库。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("client_message_id", sa.String(length=64), nullable=True),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_messages_client_message_id "
        "ON messages (client_message_id) "
        "WHERE client_message_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_messages_client_message_id")
    op.drop_column("messages", "client_message_id")
