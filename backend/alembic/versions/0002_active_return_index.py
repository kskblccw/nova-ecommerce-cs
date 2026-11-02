"""add partial unique index for active returns

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-01

同一订单最多只能存在一条“待审核/已通过”的售后申请，作为审批并发执行的
数据库兜底；已取消/已拒绝的申请不占用该索引。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_return_requests_active_order "
        "ON return_requests (order_id) "
        "WHERE status IN ('待审核', '已通过')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_return_requests_active_order")
