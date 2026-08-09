#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("wp08-new-release-attempt5-repair-publisher.py")), run_name="__main__")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one generated replacement in {path}: {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


list_orders = Path("candidate/services/agent-service/src/agent_modules/ecommerce/capabilities/list_orders.py").resolve()
replace_once(
    list_orders,
    "    discovery_examples=(\n"
    "        '我买过什么', '买过什么', '买了什么', '我的订单', '查一下我的订单', '订单列表', '订单',\n"
    "        '所有订单', '全部订单', '购买记录', '最贵', '最便宜', '最新', '其中', '这些',\n"
    "        '除了', '第一个', '第二个', '待发货', '已签收', '签收',\n"
    "        '按商品查订单', '某商品的订单', '查一下某商品订单', '鼠标订单', '键盘订单',\n"
    "    ),\n",
    "    discovery_examples=(\n"
    "        '我买过什么', '我的订单', '查一下我的订单', '按商品查订单', '某商品的订单', '鼠标订单', '键盘订单', '订单列表',\n"
    "        '买过什么', '买了什么', '订单', '所有订单', '全部订单', '购买记录', '最贵', '最便宜',\n"
    "        '最新', '其中', '这些', '除了', '第一个', '第二个', '待发货', '已签收', '签收', '查一下某商品订单',\n"
    "    ),\n",
)

order_details = Path("candidate/services/agent-service/src/agent_modules/ecommerce/capabilities/get_order_details.py").resolve()
replace_once(
    order_details,
    "    exclusion_examples=('物流', '在路上', '退款进度', '售后进度', '发票进度', '按商品查订单', '某商品的订单', '查一下某商品订单', '鼠标订单', '键盘订单'),\n",
    "    exclusion_examples=('物流', '在路上', '退款进度', '按商品查订单', '鼠标订单', '某商品的订单', '售后进度', '发票进度', '查一下某商品订单', '键盘订单'),\n",
)

print("WP08_ATTEMPT5_BOUNDED_EFFECT_GUIDANCE_REORDERED")
