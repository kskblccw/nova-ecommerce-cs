"""FAQ 工具：常见问题检索与知识库兜底。"""

from langchain_core.tools import tool

from ecommerce_cs.agents.tools.common import ToolReturn, tool_result
from ecommerce_cs.db.repositories import commerce as commerce_repo
from ecommerce_cs.db.session import SessionLocal
from ecommerce_cs.rag.retriever import format_hits
from ecommerce_cs.rag.retriever import search as rag_search


@tool(response_format="content_and_artifact")
async def search_faq(query: str) -> ToolReturn:
    """在 FAQ 库中搜索常见问题答案（配送、支付、会员、账号等）。

    Args:
        query: 问题关键词，如「配送时间」「会员积分」「怎么开发票」
    """
    async with SessionLocal() as db:
        faqs = await commerce_repo.search_faqs(db, query, limit=4)
        if not faqs:
            hits = await rag_search(db, query, limit=3)
            return tool_result(
                "faq",
                format_hits(hits),
                {
                    "faqs": [],
                    "hits": [hit.to_dict() for hit in hits],
                    "query": query,
                },
            )

        data = [
            {"question": faq.question, "answer": faq.answer, "category": faq.category}
            for faq in faqs
        ]
        summary = "\n\n".join(f"Q：{item['question']}\nA：{item['answer']}" for item in data)
        return tool_result("faq", summary, {"faqs": data, "query": query})


@tool(response_format="content_and_artifact")
async def search_knowledge_base(query: str) -> ToolReturn:
    """在完整知识库中检索（商品、选购指南、售后政策、FAQ）。

    Args:
        query: 检索问题
    """
    async with SessionLocal() as db:
        hits = await rag_search(db, query, limit=4)
        return tool_result(
            "knowledge",
            format_hits(hits),
            {"hits": [hit.to_dict() for hit in hits], "query": query},
        )


FAQ_TOOLS = [search_faq, search_knowledge_base]
