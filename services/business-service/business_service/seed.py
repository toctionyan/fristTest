"""Deterministic local demo seed data.

Production deployments can omit this module; it has no HTTP or Agent coupling.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .database import BusinessDatabase, utcnow


def seed_demo_data(db: BusinessDatabase) -> None:
    with db.transaction() as conn:
        if conn.execute("SELECT COUNT(*) AS cnt FROM accounts").fetchone()["cnt"]:
            return
        now = utcnow()
        accounts = [
            ("u001", "default", "customer", "张三", "[]", now),
            ("u002", "default", "customer", "李四", "[]", now),
            ("u003", "tenant-B", "customer", "王五", "[]", now),
            (
                "operator001",
                "default",
                "operator",
                "客服一号",
                '["business:read_any","refund:review","after_sales:review","invoice:review","complaint:review","support:handoff:review","complaint:create_on_behalf","support:handoff:create_on_behalf","refund:create_on_behalf","after_sales:create_on_behalf","invoice:create_on_behalf"]',
                now,
            ),
            ("admin001", "default", "admin", "管理员", '["*"]', now),
        ]
        conn.executemany(
            "INSERT INTO accounts(user_id,tenant_id,role,display_name,permissions_json,created_at) VALUES(?,?,?,?,?,?)",
            accounts,
        )
        products = [
            (
                "p001",
                "蓝牙耳机",
                "数码",
                199.0,
                58,
                12,
                1,
                1,
                "支持主动降噪与蓝牙连接",
                now,
            ),
            ("p002", "机械键盘", "数码", 399.0, 35, 12, 1, 1, "87 键机械键盘", now),
            ("p003", "无线鼠标", "数码", 99.0, 80, 12, 1, 1, "静音无线鼠标", now),
            (
                "p004",
                "定制马克杯",
                "定制家居",
                59.0,
                20,
                0,
                0,
                0,
                "定制商品，不支持无理由退货",
                now,
            ),
            ("p005", "旅行箱", "出行", 459.0, 15, 24, 1, 1, "20 寸旅行箱", now),
        ]
        conn.executemany(
            "INSERT INTO products(product_id,product_name,category,price,stock,warranty_months,returnable,support_after_sales,description,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            products,
        )
        signed = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        orders = [
            (
                "10001",
                "u001",
                "default",
                "p001",
                "蓝牙耳机",
                "已发货",
                199.0,
                1,
                None,
                "Phoenix, AZ, 1st Avenue",
                now,
                now,
            ),
            (
                "10002",
                "u001",
                "default",
                "p002",
                "机械键盘",
                "已签收",
                399.0,
                1,
                signed,
                "Phoenix, AZ, 2nd Avenue",
                now,
                now,
            ),
            (
                "10003",
                "u001",
                "default",
                "p003",
                "无线鼠标",
                "待发货",
                99.0,
                1,
                None,
                "Phoenix, AZ, 3rd Avenue",
                now,
                now,
            ),
            (
                "10004",
                "u001",
                "default",
                "p004",
                "定制马克杯",
                "已签收",
                59.0,
                1,
                signed,
                "Phoenix, AZ, 4th Avenue",
                now,
                now,
            ),
            (
                "20001",
                "u002",
                "default",
                "p005",
                "旅行箱",
                "待发货",
                459.0,
                1,
                None,
                "Phoenix, AZ, 5th Avenue",
                now,
                now,
            ),
            (
                "30001",
                "u003",
                "tenant-B",
                "p005",
                "旅行箱",
                "待发货",
                459.0,
                1,
                None,
                "Phoenix, AZ, Tenant B Avenue",
                now,
                now,
            ),
        ]
        conn.executemany(
            "INSERT INTO orders(order_id,user_id,tenant_id,product_id,product_name,status,amount,paid,signed_at,shipping_address,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            orders,
        )
        logistics = [
            ("10001", "运输中", "已到达 Phoenix 分拨中心", "预计 2 天内送达", now),
            ("10002", "已签收", "用户已签收", "已送达", now),
            ("10003", "待发货", "商家正在备货", "预计 24 小时内发货", now),
            ("10004", "已签收", "用户已签收", "已送达", now),
            ("20001", "待发货", "商家正在备货", "预计 48 小时内发货", now),
            ("30001", "待发货", "商家正在备货", "预计 48 小时内发货", now),
        ]
        conn.executemany(
            "INSERT INTO logistics(order_id,status,latest,eta,updated_at) VALUES(?,?,?,?,?)",
            logistics,
        )
        conn.execute(
            "INSERT INTO coupons(coupon_id,user_id,title,status,discount_desc,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                "CPN-001",
                "u001",
                "满 199 减 20",
                "可用",
                "满199减20",
                (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                now,
            ),
        )


# ------------------------------ app/routes ----------------------------------
