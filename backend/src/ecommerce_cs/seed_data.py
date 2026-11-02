"""种子数据：用户、商品、订单、FAQ，可选构建知识库索引。

用法：
    python -m ecommerce_cs.seed_data
    python -m ecommerce_cs.seed_data --reset
    python -m ecommerce_cs.seed_data --reset --with-index
"""

import argparse
import asyncio
import random
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from ecommerce_cs.catalog_data import PRODUCTS
from ecommerce_cs.core.security import hash_password, hash_phone, mask_phone
from ecommerce_cs.db.models import (
    FAQ,
    AnswerFeedback,
    AuditEvent,
    AuthSession,
    Conversation,
    ConversationSummary,
    HandoffTicket,
    Message,
    Order,
    OrderItem,
    PendingAction,
    Product,
    RagChunk,
    RagDocument,
    RestockSubscription,
    RetrievalLog,
    ReturnRequest,
    User,
    UserProfile,
)
from ecommerce_cs.db.session import SessionLocal

PASSWORD = "shop123456"

USER_NAMES = [
    ("张伟", "13800001001"),
    ("李娜", "13800001002"),
    ("王强", "13800001003"),
    ("赵敏", "13800001004"),
    ("刘洋", "13800001005"),
    ("陈静", "13800001006"),
    ("孙磊", "13800001007"),
    ("周婷", "13800001008"),
    ("吴昊", "13800001009"),
    ("黄丽", "13800001010"),
    ("马超", "13900002001"),
    ("林黛玉", "13900002002"),
    ("郭靖", "13900002003"),
    ("杨过", "13900002004"),
    ("小龙女", "13900002005"),
    ("张三丰", "13900002006"),
    ("令狐冲", "13900002007"),
    ("任盈盈", "13900002008"),
    ("韦小宝", "13900002009"),
    ("康熙", "13900002010"),
]

ADDRESSES = [
    "北京市朝阳区建国路88号 SOHO现代城A座1201",
    "上海市浦东新区陆家嘴环路1000号 恒生银行大厦15F",
    "广州市天河区体育西路111号 维多利广场B座2208",
    "深圳市南山区科技园南路2号 软件产业基地4栋A单元",
    "杭州市西湖区文三路456号 东部软件园科技大厦9层",
    "成都市武侯区人民南路四段19号 威斯顿联邦大厦7F",
    "南京市鼓楼区中山北路28号 江苏商厦1305",
    "武汉市洪山区珞喻路1037号 华中科技大学科技园",
]

FAQS = [
    ("下单后多久发货？", "工作日 15:00 前下单当天发出，之后次日发出；节假日顺延。", "配送物流"),
    ("配送需要多少天？", "主要城市 1-3 天送达，一线城市支持次日达，偏远地区 5-7 天。", "配送物流"),
    ("如何查询物流信息？", "在「我的订单」中查看物流详情，也可用运单号在快递官网查询。", "配送物流"),
    ("能否指定配送时间？", "部分城市支持精准达，可指定时间段，需额外服务费。", "配送物流"),
    ("可以修改收货地址吗？", "订单未发货可修改地址，已发货无法修改，可联系客服协助转寄。", "配送物流"),
    ("快递丢件怎么办？", "超过预计时效 3 天未更新物流请联系客服，确认后可退款或补发。", "配送物流"),
    ("可以自提吗？", "部分城市支持门店自提，商品到店后短信通知，凭取货码 7 天内提取。", "配送物流"),
    ("退换货政策是什么？", "7 天无理由退货（商品完好），15 天质量问题换货，生鲜与个护不支持无理由。", "售后政策"),
    ("如何申请退货？", "在订单详情页申请售后，选择退货类型并填写原因，审核通过后寄回。", "售后政策"),
    ("退款什么时候到账？", "支付宝/微信即时到账，银行卡 1-3 个工作日，信用卡 3-7 个工作日。", "售后政策"),
    ("商品价格保护政策？", "下单 7 天内降价可申请价保，秒杀/拼团/限时抢购除外。", "售后政策"),
    ("收到商品有质量问题怎么办？", "签收 24 小时内拍照取证并联系客服，可换新、退货退款或部分补偿。", "售后政策"),
    ("换货的流程是什么？", "提交申请→审核→寄回商品→仓库验收→发出新货，通常 3-5 个工作日。", "售后政策"),
    ("过了 7 天还能退货吗？", "超过 7 天有质量问题可在 15 天内换货或维修，保修期内可申请保修。", "售后政策"),
    ("支持哪些支付方式？", "支付宝、微信支付、银联卡、Apple Pay、花呗分期、京东白条、云闪付。", "支付相关"),
    ("如何申请分期付款？", "下单选择花呗或信用卡分期，可选 3/6/12 期，3 期免息。", "支付相关"),
    ("支付失败怎么办？", "检查余额、限额与网络，或更换支付方式；仍有问题请联系客服。", "支付相关"),
    ("花呗分期手续费怎么算？", "3 期免手续费，6 期每期 0.6%，12 期每期 0.5%。", "支付相关"),
    ("会员有什么权益？", "银卡 98 折、金卡 95 折、钻石 9 折，积分抵现、生日双倍积分、优先发货。", "会员权益"),
    ("如何升级会员等级？", "年消费满 2000 元升金卡，满 5000 元升钻石卡，每年 1 月重新评定。", "会员权益"),
    ("积分如何获取和使用？", "消费 1 元得 1 积分，100 积分抵 1 元，有效期 1 年。", "会员权益"),
    ("如何取消订单？", "未发货可在订单详情页取消，款项即时退回；已发货需收货后申请退货。", "订单相关"),
    ("发票如何开具？", "下单可选择电子或纸质发票，电子发票确认收货后发送至邮箱。", "订单相关"),
    ("可以修改订单中的商品吗？", "未发货可取消重下，已发货无法修改商品。", "订单相关"),
    ("如何修改绑定的手机号？", "在账户设置-安全中心修改，需原手机号验证码；停用可联系客服核验。", "账号安全"),
    ("账户被盗怎么办？", "立即联系客服冻结账户，修改密码并检查登录设备与支付设置。", "账号安全"),
    ("隐私数据如何保护？", "手机号加密存储，客服系统不向模型暴露手机号，日志统一脱敏。", "账号安全"),
    ("如何查看商品是否正品？", "商品均为品牌直供或授权经销，支持专柜验货与防伪码验证。", "购物指南"),
    ("可以送礼包装吗？", "下单可选择礼品包装服务，含礼盒、缎带与贺卡。", "购物指南"),
    ("有没有学生优惠？", "在校学生凭学信网认证可享 95 折，认证后自动生效。", "购物指南"),
    ("如何联系人工客服？", "在线客服每天 9:00-22:00，热线 400-888-8888，智能客服 7×24 小时在线。", "其他"),
    ("你们的营业时间是？", "在线客服 9:00-22:00，订单系统 7×24 小时可用。", "其他"),
    ("企业团购有优惠吗？", "单笔 50 件以上或金额 5000 元以上可享额外折扣，请联系企业客服。", "其他"),
]

STATUSES = ["待付款", "已发货", "已完成", "已取消"]
CARRIERS = ["SF", "YT", "ZTO", "JD", "DB"]


async def _reset(db) -> None:
    for model in (
        AnswerFeedback,
        AuditEvent,
        HandoffTicket,
        RestockSubscription,
        PendingAction,
        Message,
        ConversationSummary,
        Conversation,
        AuthSession,
        UserProfile,
        RetrievalLog,
        RagChunk,
        RagDocument,
        ReturnRequest,
        OrderItem,
        Order,
        Product,
        FAQ,
        User,
    ):
        await db.execute(delete(model))
    await db.commit()
    print("旧数据已清除")


async def _seed_users(db) -> list[User]:
    password_hash = hash_password(PASSWORD)
    users: list[User] = []
    for name, phone in USER_NAMES:
        users.append(
            User(
                name=name,
                phone_hash=hash_phone(phone),
                phone_masked=mask_phone(phone),
                password_hash=password_hash,
                role="user",
            )
        )
    users.append(
        User(
            name="管理员",
            phone_hash=hash_phone("13900000000"),
            phone_masked=mask_phone("13900000000"),
            password_hash=password_hash,
            role="admin",
        )
    )
    db.add_all(users)
    await db.commit()
    for user in users:
        await db.refresh(user)
    return users


async def _seed_products(db) -> list[Product]:
    products = [
        Product(
            name=item["name"],
            description=item["description"],
            price=item["price"],
            stock=item["stock"],
            category=item["category"],
            specs=item.get("specs", {}),
        )
        for item in PRODUCTS
    ]
    db.add_all(products)
    await db.commit()
    for product in products:
        await db.refresh(product)
    return products


async def _seed_orders(db, users: list[User], products: list[Product]) -> None:
    rng = random.Random(20260716)
    today = datetime.now(UTC)

    # 张伟的演示订单：待付款 / 已发货 / 已完成 / 已取消
    first = users[0]
    demo_status = ["待付款", "已发货", "已完成", "已取消"]
    for index, status in enumerate(demo_status, start=1):
        selected = [products[index * 3], products[index * 3 + 1]]
        total = sum(product.price for product in selected)
        tracking = f"{CARRIERS[index % 5]}{rng.randint(10**9, 10**10 - 1)}" if status in ("已发货", "已完成") else None
        order = Order(
            order_no=f"ORD2026071600{index:02d}",
            user_id=first.id,
            status=status,
            total=round(total, 2),
            address=ADDRESSES[index % len(ADDRESSES)],
            tracking_no=tracking,
            created_at=today - timedelta(days=index * 3),
        )
        db.add(order)
        await db.flush()
        for product in selected:
            db.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    quantity=1,
                    unit_price=product.price,
                )
            )

    for index in range(40):
        user = users[(index + 2) % len(users)]
        status = STATUSES[index % len(STATUSES)]
        picks = [
            (product, rng.randint(1, 2))
            for product in rng.sample(products, rng.randint(1, 3))
        ]
        total = sum(product.price * quantity for product, quantity in picks)
        tracking = (
            f"{CARRIERS[index % 5]}{rng.randint(10**9, 10**10 - 1)}"
            if status in ("已发货", "已完成")
            else None
        )
        order = Order(
            order_no=f"ORD2026071690{index:02d}",
            user_id=user.id,
            status=status,
            total=round(total, 2),
            address=ADDRESSES[index % len(ADDRESSES)],
            tracking_no=tracking,
            created_at=today - timedelta(days=rng.randint(1, 90)),
        )
        db.add(order)
        await db.flush()
        for product, quantity in picks:
            db.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    quantity=quantity,
                    unit_price=product.price,
                )
            )

    await db.commit()


async def _seed_faqs(db) -> None:
    db.add_all(
        [FAQ(question=question, answer=answer, category=category) for question, answer, category in FAQS]
    )
    await db.commit()


async def seed(reset: bool, with_index: bool) -> None:
    async with SessionLocal() as db:
        if reset:
            await _reset(db)
        else:
            count = int(await db.scalar(select(func.count(User.id))) or 0)
            if count > 0:
                print("数据库已有用户数据，跳过（使用 --reset 重置）")
                return

        users = await _seed_users(db)
        products = await _seed_products(db)
        await _seed_orders(db, users, products)
        await _seed_faqs(db)

        print(f"[Seed] 用户 {len(users)}，商品 {len(products)}，订单 44，FAQ {len(FAQS)}")
        print("[Seed] 演示账号：13800001001 / shop123456")
        print("[Seed] 管理员：13900000000 / shop123456")

        if with_index:
            from ecommerce_cs.rag.indexer import rebuild_index

            stats = await rebuild_index(db)
            print(f"[Index] 文档 {stats['documents']}，切片 {stats['chunks']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--with-index", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(seed(args.reset, args.with_index))
    except Exception as exc:
        print(f"[Seed] 失败：{exc}")
        print("请确认：已执行 alembic upgrade head，且 .env 配置正确")
        sys.exit(1)


if __name__ == "__main__":
    main()
