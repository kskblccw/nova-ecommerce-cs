"""baseline: 冻结的初始 schema DDL

Revision ID: 0001
Revises:
Create Date: 2026-10-31

说明：baseline 不再引用运行时 ORM 元数据，避免模型变化后“新建库”和
“增量迁移库”结构不一致。后续 schema 变更请新增独立 migration。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASELINE_SQL = """
CREATE TABLE faqs (
    id SERIAL NOT NULL,
    question VARCHAR(300) NOT NULL,
    answer TEXT NOT NULL,
    category VARCHAR(50) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE products (
    id SERIAL NOT NULL,
    name VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    price FLOAT NOT NULL,
    stock INTEGER NOT NULL,
    category VARCHAR(50) NOT NULL,
    specs JSONB NOT NULL,
    image_url VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE rag_documents (
    id VARCHAR(36) NOT NULL,
    title VARCHAR(200) NOT NULL,
    category VARCHAR(50) NOT NULL,
    doc_type VARCHAR(40) NOT NULL,
    content TEXT NOT NULL,
    source VARCHAR(100) NOT NULL,
    meta JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE retrieval_logs (
    id VARCHAR(36) NOT NULL,
    query VARCHAR(300) NOT NULL,
    top_score FLOAT NOT NULL,
    hits INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE users (
    id SERIAL NOT NULL,
    name VARCHAR(50) NOT NULL,
    phone_hash VARCHAR(64) NOT NULL,
    phone_masked VARCHAR(20) NOT NULL,
    password_hash VARCHAR(128) NOT NULL,
    role VARCHAR(10) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE answer_feedback (
    id VARCHAR(36) NOT NULL,
    message_id VARCHAR(36) NOT NULL,
    conversation_id VARCHAR(36) NOT NULL,
    user_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    comment TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_answer_feedback_message UNIQUE (message_id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE audit_events (
    id VARCHAR(36) NOT NULL,
    user_id INTEGER,
    action VARCHAR(60) NOT NULL,
    resource_type VARCHAR(40) NOT NULL,
    resource_id VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE TABLE auth_sessions (
    id VARCHAR(36) NOT NULL,
    user_id INTEGER NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    UNIQUE (token_hash)
);

CREATE TABLE conversations (
    id VARCHAR(36) NOT NULL,
    user_id INTEGER NOT NULL,
    title VARCHAR(120) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_message_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE orders (
    id SERIAL NOT NULL,
    order_no VARCHAR(30) NOT NULL,
    user_id INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL,
    total FLOAT NOT NULL,
    address VARCHAR(300) NOT NULL,
    tracking_no VARCHAR(50),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE rag_chunks (
    id VARCHAR(36) NOT NULL,
    document_id VARCHAR(36) NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rag_chunk_doc_index UNIQUE (document_id, chunk_index),
    FOREIGN KEY(document_id) REFERENCES rag_documents (id) ON DELETE CASCADE
);

CREATE TABLE restock_subscriptions (
    id VARCHAR(36) NOT NULL,
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_restock_user_product UNIQUE (user_id, product_id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE
);

CREATE TABLE user_profiles (
    user_id INTEGER NOT NULL,
    preferences JSONB NOT NULL,
    facts JSONB NOT NULL,
    weight FLOAT NOT NULL,
    last_active_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (user_id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE conversation_summaries (
    conversation_id VARCHAR(36) NOT NULL,
    summary TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (conversation_id),
    FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE TABLE handoff_tickets (
    id VARCHAR(36) NOT NULL,
    conversation_id VARCHAR(36) NOT NULL,
    user_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    resolved_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE messages (
    id VARCHAR(36) NOT NULL,
    conversation_id VARCHAR(36) NOT NULL,
    role VARCHAR(10) NOT NULL,
    content TEXT NOT NULL,
    parts JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE TABLE order_items (
    id SERIAL NOT NULL,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price FLOAT NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(order_id) REFERENCES orders (id) ON DELETE CASCADE,
    FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE RESTRICT
);

CREATE TABLE pending_actions (
    id VARCHAR(36) NOT NULL,
    conversation_id VARCHAR(36) NOT NULL,
    user_id INTEGER NOT NULL,
    type VARCHAR(30) NOT NULL,
    summary TEXT NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) NOT NULL,
    idempotency_key VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    resolved_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    UNIQUE (idempotency_key)
);

CREATE TABLE return_requests (
    id SERIAL NOT NULL,
    return_no VARCHAR(30) NOT NULL,
    order_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    type VARCHAR(20) NOT NULL,
    reason TEXT NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(order_id) REFERENCES orders (id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_faqs_category ON faqs (category);

CREATE INDEX ix_products_category ON products (category);

CREATE INDEX ix_rag_documents_type_category ON rag_documents (doc_type, category);

CREATE UNIQUE INDEX ix_users_phone_hash ON users (phone_hash);

CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);

CREATE INDEX ix_conversations_user_last ON conversations (user_id, last_message_at);

CREATE UNIQUE INDEX ix_orders_order_no ON orders (order_no);

CREATE INDEX ix_orders_user_id ON orders (user_id);

CREATE INDEX ix_rag_chunks_document_id ON rag_chunks (document_id);

CREATE INDEX ix_messages_conversation_created ON messages (conversation_id, created_at);

CREATE INDEX ix_order_items_order_id ON order_items (order_id);

CREATE INDEX ix_pending_actions_conversation_status ON pending_actions (conversation_id, status);

CREATE INDEX ix_return_requests_order_id ON return_requests (order_id);

CREATE UNIQUE INDEX ix_return_requests_return_no ON return_requests (return_no);

CREATE INDEX ix_return_requests_user_id ON return_requests (user_id);
"""

TABLES = (
    "faqs",
    "products",
    "rag_documents",
    "retrieval_logs",
    "users",
    "answer_feedback",
    "audit_events",
    "auth_sessions",
    "conversations",
    "orders",
    "rag_chunks",
    "restock_subscriptions",
    "user_profiles",
    "conversation_summaries",
    "handoff_tickets",
    "messages",
    "order_items",
    "pending_actions",
    "return_requests",
)


def upgrade() -> None:
    for statement in BASELINE_SQL.split(";\n\n"):
        statement = statement.strip()
        if statement:
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
