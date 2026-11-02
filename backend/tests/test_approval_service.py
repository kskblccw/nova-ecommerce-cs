"""审批执行层测试：归属二次校验与未知动作处理。"""

from types import SimpleNamespace

import pytest

from ecommerce_cs.services import approval as approval_service


@pytest.mark.asyncio
async def test_unknown_action_type_returns_failure():
    action = SimpleNamespace(type="delete_everything", payload={})

    ok, message = await approval_service._execute(None, action, 1)

    assert ok is False
    assert "不支持的操作类型" in message


@pytest.mark.asyncio
async def test_cancel_return_rejects_foreign_request(monkeypatch):
    async def fake_get_return(db, return_no):
        return SimpleNamespace(user_id=999, status="待审核", return_no=return_no)

    monkeypatch.setattr(
        approval_service.commerce_repo, "get_return_by_no", fake_get_return
    )
    action = SimpleNamespace(type="cancel_return", payload={"return_no": "RTN1"})

    ok, message = await approval_service._execute(None, action, 1)

    assert ok is False
    assert "不属于当前用户" in message


@pytest.mark.asyncio
async def test_cancel_return_of_own_request_succeeds(monkeypatch):
    async def fake_get_return(db, return_no):
        return SimpleNamespace(user_id=1, status="待审核", return_no=return_no)

    async def fake_cancel(db, return_no):
        return True

    async def fake_audit(db, **kwargs):
        return None

    monkeypatch.setattr(
        approval_service.commerce_repo, "get_return_by_no", fake_get_return
    )
    monkeypatch.setattr(
        approval_service.commerce_repo, "cancel_return_request", fake_cancel
    )
    monkeypatch.setattr(approval_service.ops_repo, "audit", fake_audit)
    action = SimpleNamespace(type="cancel_return", payload={"return_no": "RTN1"})

    ok, message = await approval_service._execute(None, action, 1)

    assert ok is True
    assert "已取消" in message


@pytest.mark.asyncio
async def test_cancel_return_reports_missing_request(monkeypatch):
    async def fake_get_return(db, return_no):
        return None

    monkeypatch.setattr(
        approval_service.commerce_repo, "get_return_by_no", fake_get_return
    )
    action = SimpleNamespace(type="cancel_return", payload={"return_no": "RTN404"})

    ok, message = await approval_service._execute(None, action, 1)

    assert ok is False
    assert "不存在" in message
