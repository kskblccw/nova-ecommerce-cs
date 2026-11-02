"""商品域工具：搜索、详情、对比、缺货订阅与知识检索。"""

from langchain_core.tools import tool

from ecommerce_cs.agents.tools.common import (
    ToolReturn,
    current_user_id,
    error_result,
    format_money,
    login_required,
    product_public,
    tool_result,
)
from ecommerce_cs.db.repositories import commerce as commerce_repo
from ecommerce_cs.db.session import SessionLocal
from ecommerce_cs.rag.retriever import format_hits
from ecommerce_cs.rag.retriever import search as rag_search


@tool(response_format="content_and_artifact")
async def search_products(keyword: str) -> ToolReturn:
    """按关键词搜索商品，返回商品卡片数据（价格、库存、分类）。

    Args:
        keyword: 搜索关键词，如「降噪耳机」「跑鞋」「猫粮」
    """
    async with SessionLocal() as db:
        products = await commerce_repo.search_products(db, keyword, limit=6)
        if not products:
            return tool_result(
                "products",
                f"未找到与「{keyword}」相关的商品，可尝试换一个关键词。",
                {"products": []},
            )
        data = [product_public(product) for product in products]
        summary = f"找到 {len(data)} 个与「{keyword}」相关的商品：\n" + "\n".join(
            f"{item['name']}｜{format_money(item['price'])}｜库存 {item['stock']}"
            for item in data
        )
        return tool_result("products", summary, {"products": data, "keyword": keyword})


@tool(response_format="content_and_artifact")
async def get_product_detail(product_id: int) -> ToolReturn:
    """查询单个商品的详细信息与规格参数。

    Args:
        product_id: 商品 ID
    """
    async with SessionLocal() as db:
        product = await commerce_repo.get_product(db, product_id)
        if not product:
            return error_result(f"未找到商品 ID {product_id}。", "not_found")

        specs = product.specs or {}
        data = product_public(product)
        summary = (
            f"{product.name}｜{format_money(product.price)}｜库存 {product.stock}\n"
            f"{product.description}\n"
            + "；".join(f"{key}：{value}" for key, value in specs.items())
        )
        return tool_result("product_detail", summary, data)


@tool(response_format="content_and_artifact")
async def compare_products(product_ids: list[int]) -> ToolReturn:
    """对比 2-4 件商品的核心参数，帮助用户做购买决策。

    Args:
        product_ids: 商品 ID 列表（2-4 个）
    """
    if len(product_ids) < 2 or len(product_ids) > 4:
        return error_result("请提供 2-4 个商品 ID 进行对比。", "invalid_args")

    async with SessionLocal() as db:
        products = await commerce_repo.list_products_by_ids(db, product_ids)
        if len(products) < 2:
            return error_result("有效商品不足 2 个，请核对商品 ID。", "not_found")

        data = [product_public(product) for product in products]
        lines = ["商品对比："]
        for item in data:
            lines.append(
                f"{item['name']}｜{format_money(item['price'])}｜库存 {item['stock']}｜"
                f"{item['category']}"
            )
        return tool_result("comparison", "\n".join(lines), {"products": data})


@tool(response_format="content_and_artifact")
async def subscribe_restock(product_id: int) -> ToolReturn:
    """为缺货商品订阅到货提醒。

    Args:
        product_id: 商品 ID
    """
    guard = login_required()
    if guard:
        return guard
    user_id = int(current_user_id() or 0)

    async with SessionLocal() as db:
        product = await commerce_repo.get_product(db, product_id)
        if not product:
            return error_result(f"未找到商品 ID {product_id}。", "not_found")
        created = await commerce_repo.subscribe_restock(db, user_id, product_id)
        if not created:
            return tool_result(
                "info", f"您已订阅「{product.name}」的到货提醒，补货后会通知您。", None
            )
        return tool_result(
            "info", f"已订阅「{product.name}」的到货提醒，补货后会第一时间通知您。", None
        )


@tool(response_format="content_and_artifact")
async def search_knowledge(query: str) -> ToolReturn:
    """检索商品知识库：选购建议、使用方法、保养维护与售后政策。

    Args:
        query: 检索问题，如「降噪耳机怎么选」「牛排怎么保存」
    """
    async with SessionLocal() as db:
        hits = await rag_search(db, query, limit=4)
        return tool_result(
            "knowledge",
            format_hits(hits),
            {"hits": [hit.to_dict() for hit in hits], "query": query},
        )


PRODUCT_TOOLS = [
    search_products,
    get_product_detail,
    compare_products,
    subscribe_restock,
    search_knowledge,
]
