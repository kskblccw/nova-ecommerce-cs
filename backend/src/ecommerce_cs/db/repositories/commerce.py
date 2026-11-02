"""商品、订单、售后与 FAQ 的仓储。"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.db.models import (
    FAQ,
    Order,
    OrderItem,
    Product,
    RestockSubscription,
    ReturnRequest,
)


async def search_products(db: AsyncSession, keyword: str, limit: int = 6) -> list[Product]:
    pattern = f"%{keyword}%"
    result = await db.scalars(
        select(Product)
        .where(
            or_(
                Product.name.ilike(pattern),
                Product.description.ilike(pattern),
                Product.category.ilike(pattern),
            )
        )
        .order_by(Product.stock.desc())
        .limit(limit)
    )
    return list(result.all())


async def list_products_by_ids(db: AsyncSession, product_ids: list[int]) -> list[Product]:
    if not product_ids:
        return []
    result = await db.scalars(select(Product).where(Product.id.in_(product_ids)))
    return list(result.all())


async def get_product(db: AsyncSession, product_id: int) -> Product | None:
    return await db.scalar(select(Product).where(Product.id == product_id))


async def list_user_orders(db: AsyncSession, user_id: int) -> list[Order]:
    result = await db.scalars(
        select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc())
    )
    return list(result.all())


async def get_order_by_no(db: AsyncSession, order_no: str) -> Order | None:
    return await db.scalar(select(Order).where(Order.order_no == order_no))


async def get_order_items(db: AsyncSession, order_id: int) -> list[tuple[OrderItem, Product]]:
    rows = await db.execute(
        select(OrderItem, Product)
        .join(Product, Product.id == OrderItem.product_id)
        .where(OrderItem.order_id == order_id)
    )
    return [(item, product) for item, product in rows.all()]


async def get_order_items_bulk(
    db: AsyncSession, order_ids: list[int]
) -> dict[int, list[tuple[OrderItem, Product]]]:
    if not order_ids:
        return {}
    rows = await db.execute(
        select(OrderItem, Product)
        .join(Product, Product.id == OrderItem.product_id)
        .where(OrderItem.order_id.in_(order_ids))
    )
    grouped: dict[int, list[tuple[OrderItem, Product]]] = {}
    for item, product in rows.all():
        grouped.setdefault(item.order_id, []).append((item, product))
    return grouped


async def update_order_address(db: AsyncSession, order_id: int, address: str) -> None:
    await db.execute(
        update(Order)
        .where(Order.id == order_id)
        .values(address=address, updated_at=datetime.now(UTC))
    )
    await db.commit()


async def cancel_order(db: AsyncSession, order_id: int) -> None:
    await db.execute(
        update(Order)
        .where(Order.id == order_id)
        .values(status="已取消", updated_at=datetime.now(UTC))
    )
    await db.commit()


async def list_user_returns(db: AsyncSession, user_id: int) -> list[ReturnRequest]:
    result = await db.scalars(
        select(ReturnRequest)
        .where(ReturnRequest.user_id == user_id)
        .order_by(ReturnRequest.created_at.desc())
    )
    return list(result.all())


async def get_return_by_no(db: AsyncSession, return_no: str) -> ReturnRequest | None:
    return await db.scalar(select(ReturnRequest).where(ReturnRequest.return_no == return_no))


async def has_active_return(db: AsyncSession, order_id: int) -> bool:
    row = await db.scalar(
        select(ReturnRequest.id)
        .where(
            ReturnRequest.order_id == order_id,
            ReturnRequest.status.in_(("待审核", "已通过")),
        )
        .limit(1)
    )
    return row is not None


async def create_return_request(
    db: AsyncSession,
    *,
    order_id: int,
    user_id: int,
    return_type: str,
    reason: str,
) -> ReturnRequest:
    return_no = (
        f"RTN{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        f"{uuid.uuid4().hex[:6].upper()}"
    )
    request = ReturnRequest(
        return_no=return_no,
        order_id=order_id,
        user_id=user_id,
        type=return_type,
        reason=reason,
        status="待审核",
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)
    return request


async def cancel_return_request(db: AsyncSession, return_no: str) -> bool:
    row = await get_return_by_no(db, return_no)
    if not row or row.status != "待审核":
        return False
    row.status = "已取消"
    await db.commit()
    return True


async def delete_returns_for_order(db: AsyncSession, order_id: int) -> None:
    await db.execute(delete(ReturnRequest).where(ReturnRequest.order_id == order_id))
    await db.commit()


async def search_faqs(db: AsyncSession, query: str, limit: int = 5) -> list[FAQ]:
    pattern = f"%{query}%"
    result = await db.scalars(
        select(FAQ)
        .where(
            or_(
                FAQ.question.ilike(pattern),
                FAQ.answer.ilike(pattern),
                FAQ.category.ilike(pattern),
            )
        )
        .limit(limit)
    )
    return list(result.all())


async def faq_categories(db: AsyncSession) -> list[str]:
    result = await db.scalars(select(FAQ.category).distinct())
    return list(result.all())


async def subscribe_restock(db: AsyncSession, user_id: int, product_id: int) -> bool:
    exists = await db.scalar(
        select(RestockSubscription).where(
            RestockSubscription.user_id == user_id,
            RestockSubscription.product_id == product_id,
        )
    )
    if exists:
        return False
    db.add(RestockSubscription(user_id=user_id, product_id=product_id))
    await db.commit()
    return True
