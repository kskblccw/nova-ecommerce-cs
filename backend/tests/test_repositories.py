"""仓储层测试：用户、会话令牌与知识库文档（不依赖真实数据库）。"""

from datetime import UTC, datetime

import pytest

from ecommerce_cs.db.models import AuthSession, RagDocument, User
from ecommerce_cs.db.repositories import knowledge as knowledge_repo
from ecommerce_cs.db.repositories import users as users_repo


class _Result:
    def __init__(self, rows=(), rowcount: int = 0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, *, scalar=None, scalars_rows=(), execute_results=(), rowcount: int = 0):
        self.scalar_value = scalar
        self.scalars_rows = list(scalars_rows)
        self.execute_results = list(execute_results)
        self.rowcount = rowcount
        self.added: list = []
        self.commits = 0
        self.refreshed: list = []

    async def scalar(self, _stmt):
        return self.scalar_value

    async def scalars(self, _stmt):
        return _Result(self.scalars_rows)

    async def execute(self, _stmt, _params=None):
        if self.execute_results:
            item = self.execute_results.pop(0)
            return item if isinstance(item, _Result) else _Result(item)
        return _Result(rowcount=self.rowcount)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)


# ── 用户与会话 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_lookups_return_scalar():
    user = User(name="张三", phone_hash="h", phone_masked="138****0000", password_hash="p")
    db = _FakeSession(scalar=user)

    assert await users_repo.get_user_by_phone_hash(db, "h") is user
    assert await users_repo.get_user_by_id(db, 1) is user


@pytest.mark.asyncio
async def test_create_user_persists_and_refreshes():
    db = _FakeSession()

    user = await users_repo.create_user(
        db,
        name="张三",
        phone_hash="h",
        phone_masked="138****0000",
        password_hash="p",
    )

    assert user.role == "user"
    assert db.added == [user]
    assert db.commits == 1
    assert db.refreshed == [user]


@pytest.mark.asyncio
async def test_update_password_executes_and_commits():
    db = _FakeSession()

    await users_repo.update_password(db, 1, "new-hash")

    assert db.commits == 1
    assert db.execute_results == []


@pytest.mark.asyncio
async def test_create_auth_session_sets_expiry():
    db = _FakeSession()

    session = await users_repo.create_auth_session(db, user_id=1, token_hash="t")

    assert session.user_id == 1
    assert session.token_hash == "t"
    assert session.expires_at > datetime.now(UTC)
    assert db.added == [session]


@pytest.mark.asyncio
async def test_get_active_session_returns_scalar():
    session = AuthSession(user_id=1, token_hash="t", expires_at=datetime.now(UTC))
    db = _FakeSession(scalar=session)

    assert await users_repo.get_active_session(db, "t") is session


@pytest.mark.asyncio
async def test_revoke_session_and_user_sessions_commit():
    db = _FakeSession()

    await users_repo.revoke_session(db, "sid")
    await users_repo.revoke_user_sessions(db, 1)

    assert db.commits == 2


# ── 知识库文档 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_documents_returns_rows_and_total():
    documents = [RagDocument(id="d1", title="a", content="x", category="通用")]
    db = _FakeSession(scalar=1, scalars_rows=documents)

    rows, total = await knowledge_repo.list_documents(db)

    assert rows == documents
    assert total == 1


@pytest.mark.asyncio
async def test_get_document_returns_scalar():
    document = RagDocument(id="d1", title="a", content="x", category="通用")
    db = _FakeSession(scalar=document)

    assert await knowledge_repo.get_document(db, "d1") is document


@pytest.mark.asyncio
async def test_create_document_defaults_meta():
    db = _FakeSession()

    document = await knowledge_repo.create_document(
        db, title="标题", category="通用", doc_type="custom", content="正文"
    )

    assert document.meta == {}
    assert db.added == [document]
    assert db.refreshed == [document]


@pytest.mark.asyncio
async def test_update_document_only_applies_given_fields():
    document = RagDocument(
        id="d1", title="旧标题", content="旧正文", category="旧分类", doc_type="custom"
    )
    db = _FakeSession()

    updated = await knowledge_repo.update_document(db, document, title="新标题")

    assert updated.title == "新标题"
    assert updated.category == "旧分类"
    assert updated.content == "旧正文"
    assert updated.updated_at is not None
    assert db.commits == 1


@pytest.mark.asyncio
async def test_update_document_applies_all_fields():
    document = RagDocument(
        id="d1", title="旧标题", content="旧正文", category="旧分类", doc_type="custom"
    )
    db = _FakeSession()

    await knowledge_repo.update_document(
        db, document, title="T", category="C", doc_type="faq", content="正文"
    )

    assert (document.title, document.category, document.doc_type, document.content) == (
        "T",
        "C",
        "faq",
        "正文",
    )


@pytest.mark.asyncio
async def test_delete_documents_handles_empty_and_rows():
    db = _FakeSession(rowcount=2)

    assert await knowledge_repo.delete_documents(db, []) == 0
    assert await knowledge_repo.delete_documents(db, ["d1", "d2"]) == 2
    assert db.commits == 1


@pytest.mark.asyncio
async def test_delete_all_documents_commits():
    db = _FakeSession()

    await knowledge_repo.delete_all_documents(db)

    assert db.commits == 1


@pytest.mark.asyncio
async def test_list_chunks_returns_rows():
    chunks = [_Result()]  # 任意对象，仅验证返回列表
    db = _FakeSession(scalars_rows=chunks)

    assert await knowledge_repo.list_chunks(db) == chunks


@pytest.mark.asyncio
async def test_replace_chunks_adds_indexed_chunks():
    db = _FakeSession()

    await knowledge_repo.replace_chunks(db, "d1", [("a", [0.1]), ("b", [0.2])])

    assert [chunk.chunk_index for chunk in db.added] == [0, 1]
    assert [chunk.content for chunk in db.added] == ["a", "b"]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_knowledge_stats_aggregates_rows():
    db = _FakeSession(
        scalar=3,
        execute_results=[
            _Result([("faq", 2), ("policy", 1)]),
            _Result([("通用", 3)]),
        ],
    )

    total, by_type, by_category = await knowledge_repo.knowledge_stats(db)

    assert total == 3
    assert by_type == {"faq": 2, "policy": 1}
    assert by_category == {"通用": 3}


@pytest.mark.asyncio
async def test_log_retrieval_truncates_long_query():
    db = _FakeSession()

    await knowledge_repo.log_retrieval(db, "长" * 400, 0.9, 3)

    assert len(db.added[0].query) == 300
    assert db.commits == 1


@pytest.mark.asyncio
async def test_retrieval_summary_rounds_average():
    class _SeqSession(_FakeSession):
        def __init__(self):
            super().__init__()
            self.values = [2, 0.8766]

        async def scalar(self, _stmt):
            return self.values.pop(0)

    summary = await knowledge_repo.retrieval_summary(_SeqSession())

    assert summary == {"queries": 2, "avg_top_score": 0.877}


@pytest.mark.asyncio
async def test_retrieval_summary_handles_zero():
    db = _FakeSession(scalar=0)

    assert await knowledge_repo.retrieval_summary(db) == {"queries": 0, "avg_top_score": 0.0}
