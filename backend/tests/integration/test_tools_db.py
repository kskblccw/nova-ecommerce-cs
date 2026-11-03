"""工具层真实数据库集成测试：订单取消、售后申请与商品对比。"""

import uuid
from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import delete

from ecommerce_cs.agents.tools.aftersale import request_return
from ecommerce_cs.agents.tools.catalog import compare_products
from ecommerce_cs.agents.tools.order import cancel_order
from ecommerce_cs.core.security import hash_phone, mask_phone
from ecommerce_cs.db.models import Order, Product, ReturnRequest, User
from ecommerce_cs.db.session import SessionLocal

pytestmark = pytest.mark.integration


class ToolState(TypedDict, total=False):
    kwargs: dict
    summary: str
    artifact: dict


async def _call_tool(tool, user_id: int, **kwargs) -> dict:
    """在一个带 Runtime context 的最小图里调用工具，模拟真实 Agent 环境。"""

    async def node(state: ToolState) -> dict:
        summary, artifact = await tool.coroutine(**state["kwargs"])
        return {"summary": summary, "artifact": artifact}

    builder = StateGraph(ToolState)
    builder.add_node("call", node)
    builder.add_edge(START, "call")
    builder.add_edge("call", END)
    graph = builder.compile()
    result = await graph.ainvoke(
        {"kwargs": kwargs},
        context={"user_id": user_id, "conversation_id": "it-tools", "context_block": ""},
    )
    return result["artifact"]


class _Seeded:
    def __init__(self) -> None:
        self.user: User
        self.other: User
        self.products: list[Product] = []
        self.orders: list[Order] = []


async def _seed() -> _Seeded:
    suffix = uuid.uuid4().hex[:8]
    seeded = _Seeded()
    async with SessionLocal() as db:
        user = User(
            name="工具测试用户",
            phone_hash=hash_phone(f"198{suffix}"),
            phone_masked=mask_phone(f"198{suffix}"),
            password_hash="x",
        )
        other = User(
            name="工具测试他人",
            phone_hash=hash_phone(f"197{suffix}"),
            phone_masked=mask_phone(f"197{suffix}"),
            password_hash="x",
        )
        db.add_all([user, other])
        await db.commit()
        await db.refresh(user)
        await db.refresh(other)

        products = [
            Product(
                name=f"对比机型A-{suffix}",
                description="测试商品 A",
                price=5999.0,
                stock=10,
                category="手机数码",
                specs={"内存": "256G"},
            ),
            Product(
                name=f"对比机型B-{suffix}",
                description="测试商品 B",
                price=4999.0,
                stock=5,
                category="手机数码",
                specs={"内存": "512G"},
            ),
        ]
        db.add_all(products)
        await db.commit()
        for product in products:
            await db.refresh(product)

        orders = [
            Order(
                order_no=f"ITC{suffix}",
                user_id=user.id,
                status="待付款",
                total=1.0,
                address="测试地址",
            ),
            Order(
                order_no=f"ITS{suffix}",
                user_id=user.id,
                status="已发货",
                total=2.0,
                address="测试地址",
            ),
            Order(
                order_no=f"ITO{suffix}",
                user_id=other.id,
                status="待付款",
                total=3.0,
                address="测试地址",
            ),
            Order(
                order_no=f"ITR{suffix}",
                user_id=user.id,
                status="已完成",
                total=4.0,
                address="测试地址",
            ),
        ]
        db.add_all(orders)
        await db.commit()
        for order in orders:
            await db.refresh(order)

    seeded.user = user
    seeded.other = other
    seeded.products = products
    seeded.orders = orders
    return seeded


async def _cleanup(seeded: _Seeded) -> None:
    async with SessionLocal() as db:
        for order in seeded.orders:
            await db.execute(
                delete(ReturnRequest).where(ReturnRequest.order_id == order.id)
            )
        for order in seeded.orders:
            await db.execute(delete(Order).where(Order.id == order.id))
        for product in seeded.products:
            await db.execute(delete(Product).where(Product.id == product.id))
        await db.execute(delete(User).where(User.id.in_([seeded.user.id, seeded.other.id])))
        await db.commit()


@pytest.fixture
async def seeded(db_ready):
    data = await _seed()
    try:
        yield data
    finally:
        await _cleanup(data)


@pytest.mark.asyncio
async def test_cancel_order_creates_pending_action(seeded):
    artifact = await _call_tool(cancel_order, seeded.user.id, order_no=seeded.orders[0].order_no)

    assert artifact["kind"] == "pending_action"
    assert artifact["data"]["action"]["type"] == "cancel_order"
    assert artifact["requires_confirmation"] is True


@pytest.mark.asyncio
async def test_cancel_order_rejects_invalid_status(seeded):
    artifact = await _call_tool(cancel_order, seeded.user.id, order_no=seeded.orders[1].order_no)

    assert artifact["kind"] == "error"
    assert artifact["data"]["code"] == "invalid_status"


@pytest.mark.asyncio
async def test_cancel_order_rejects_other_users_order(seeded):
    artifact = await _call_tool(cancel_order, seeded.user.id, order_no=seeded.orders[2].order_no)

    assert artifact["kind"] == "error"
    assert artifact["data"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_compare_products_returns_comparison(seeded):
    product_ids = [product.id for product in seeded.products]

    artifact = await _call_tool(compare_products, seeded.user.id, product_ids=product_ids)

    assert artifact["kind"] == "comparison"
    names = {item["name"] for item in artifact["data"]["products"]}
    assert names == {product.name for product in seeded.products}


@pytest.mark.asyncio
async def test_compare_products_validates_argument_count(seeded):
    artifact = await _call_tool(
        compare_products, seeded.user.id, product_ids=[seeded.products[0].id]
    )

    assert artifact["kind"] == "error"
    assert artifact["data"]["code"] == "invalid_args"


@pytest.mark.asyncio
async def test_compare_products_rejects_missing_products(seeded):
    artifact = await _call_tool(compare_products, seeded.user.id, product_ids=[999_999, 999_998])

    assert artifact["kind"] == "error"
    assert artifact["data"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_request_return_creates_pending_action(seeded):
    artifact = await _call_tool(
        request_return,
        seeded.user.id,
        order_no=seeded.orders[3].order_no,
        reason="质量问题",
        return_type="退货",
    )

    assert artifact["kind"] == "pending_action"
    assert artifact["data"]["action"]["type"] == "create_return"


@pytest.mark.asyncio
async def test_request_return_rejects_unpaid_order(seeded):
    artifact = await _call_tool(
        request_return,
        seeded.user.id,
        order_no=seeded.orders[0].order_no,
        reason="不想要了",
        return_type="退款",
    )

    assert artifact["kind"] == "error"
    assert artifact["data"]["code"] == "invalid_status"


@pytest.mark.asyncio
async def test_request_return_rejects_invalid_type(seeded):
    artifact = await _call_tool(
        request_return,
        seeded.user.id,
        order_no=seeded.orders[3].order_no,
        reason="质量问题",
        return_type="维修",
    )

    assert artifact["kind"] == "error"
    assert artifact["data"]["code"] == "invalid_args"


@pytest.mark.asyncio
async def test_request_return_rejects_duplicate_in_progress(seeded):
    order = seeded.orders[3]
    async with SessionLocal() as db:
        db.add(
            ReturnRequest(
                return_no=f"RT{uuid.uuid4().hex[:10]}",
                order_id=order.id,
                user_id=seeded.user.id,
                type="退货",
                reason="质量问题",
                status="待审核",
            )
        )
        await db.commit()

    artifact = await _call_tool(
        request_return,
        seeded.user.id,
        order_no=order.order_no,
        reason="质量问题",
        return_type="退货",
    )

    assert artifact["kind"] == "error"
    assert artifact["data"]["code"] == "conflict"
