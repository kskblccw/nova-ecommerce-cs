"""确定性预路由测试（不依赖数据库与 LLM）。"""

from ecommerce_cs.agents.routing import pre_route


def test_order_routes() -> None:
    assert pre_route("帮我查一下我的订单") == "order"
    assert pre_route("我的快递到哪了") == "order"
    assert pre_route("我想修改收货地址") == "order"


def test_aftersale_routes() -> None:
    assert pre_route("我要退货退款") == "aftersale"
    assert pre_route("衣服尺码买小了想换货") == "aftersale"
    assert pre_route("商品有质量问题") == "aftersale"


def test_product_routes() -> None:
    assert pre_route("推荐一款降噪耳机") == "product"
    assert pre_route("有没有适合跑步的鞋子") == "product"
    assert pre_route("这个和那个哪个好") == "product"


def test_faq_routes() -> None:
    assert pre_route("你们支持哪些支付方式") == "faq"
    assert pre_route("会员积分怎么获取") == "faq"
    assert pre_route("你好") == "faq"


def test_human_routes_first() -> None:
    assert pre_route("我要转人工") == "human"
    assert pre_route("投诉并转人工客服") == "human"


def test_unknown_returns_none() -> None:
    assert pre_route("今天天气怎么样") is None
    assert pre_route("") is None
