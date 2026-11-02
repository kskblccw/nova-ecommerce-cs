"""确定性路由：正则优先，未命中才调用一次 LLM 分类。"""

import re

from ecommerce_cs.agents.prompts import CLASSIFIER_PROMPT
from ecommerce_cs.agents.state import IntentResult
from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.llm.circuit import ensure_available, run_llm
from ecommerce_cs.llm.factory import get_llm

log = get_logger("routing")

VALID_INTENTS = {"order", "product", "aftersale", "faq", "human"}

ROUTE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("human", re.compile(r"转人工|人工客服|真人客服|找个人|客服电话")),
    (
        "aftersale",
        re.compile(r"退货|换货|退款|退钱|售后|申请退|质量问题|坏了|投诉|差评|退换"),
    ),
    (
        "order",
        re.compile(
            r"订单|物流|快递|发货|运单|到哪了|送到了|没收到|还没到|查单|我的单|"
            r"改.*地址|修改.*地址|收货地址|取消订单|待付款|已发货"
        ),
    ),
    (
        "product",
        re.compile(
            r"推荐|有没有|多少钱|价格|有卖|买什么|选哪|对比|哪个好|哪个更|"
            r"介绍|怎么选|适合|值得买|缺货|补货|库存"
        ),
    ),
    (
        "faq",
        re.compile(
            r"支付|花呗|分期|会员|积分|营业时间|发票|配送时效|几天到|多久发货|"
            r"优惠券|学生优惠|隐私|账号|自提|怎么买|你好|在吗|谢谢|再见"
        ),
    ),
]


def pre_route(message: str) -> str | None:
    text = (message or "").strip()
    if not text:
        return None
    for intent, pattern in ROUTE_RULES:
        if pattern.search(text):
            log.info("intent_matched_by_rule", intent=intent)
            return intent
    return None


async def classify(message: str) -> str:
    """LLM 单次分类，失败或熔断时回退 faq。"""
    try:
        ensure_available()
        llm = get_llm(temperature=0).with_structured_output(IntentResult)
        result = await run_llm(
            llm.ainvoke(f"{CLASSIFIER_PROMPT}\n\n用户消息：{message}\n\n输出意图标签：")
        )
        intent = getattr(result, "intent", "") or ""
        if intent not in VALID_INTENTS:
            return "faq"
        return intent
    except Exception:
        log.exception("intent_classification_failed")
        return "faq"
