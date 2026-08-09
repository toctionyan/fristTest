"""Authoritative vertical definition for `list_orders`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .planning_contracts import verified_read_contract
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "list_orders", lambda engine: engine.execute_list_orders(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.orders.list',
    tool_name='list_orders',
    category='query',
    planner_rule='发现、筛选或列出当前用户的订单集合；当用户用商品名、状态或其他属性寻找订单本身时，即使运行时可能只命中一笔，也属于 order.list:order。也可由可见结果引用限定订单集合。',
    execution_kind='grounding_read',
    goal_completion_types=('query',),
    completion_effects=('order.list:order',),
    goal_support_types=('consult', 'action'),
    support_effects=('refund.create:order', 'refund.assess_eligibility:order', 'refund.consult_policy:order', 'invoice.create:order', 'invoice.consult_policy:order', 'after_sales.create:order', 'after_sales.consult_policy:order', 'warranty.consult_policy:order', 'order.cancel:order', 'order.query_details:order', 'order.query_logistics:order'),
    discovery_examples=(
        '我买过什么', '我的订单', '查一下我的订单', '按商品查订单', '某商品的订单', '鼠标订单', '键盘订单', '订单列表',
        '买过什么', '买了什么', '订单', '所有订单', '全部订单', '购买记录', '最贵', '最便宜',
        '最新', '其中', '这些', '除了', '第一个', '第二个', '待发货', '已签收', '签收', '查一下某商品订单',
    ),
    exclusion_examples=('物流', '在路上', '在途', '退款', '售后', '发票', '办理记录', '草稿'),
    schema=target_query_schema("list_orders", "查询当前用户订单列表或由可见结果引用限定的订单集合；同一目标包含多个集合分支时必须先用 union/intersection/difference 合成唯一 ResultRef，再以该合并集合完成最终查询。", shape="collection"),
    executor=execute,
    contract_version='2',
    planning_contract=verified_read_contract(
        resource_types=("order",), cardinality="collection", target_type="ResolvedOrderSet",
        output_name="order_list", output_type="VerifiedOrderCollection",
    ),
    presentation_contract='commerce.order_list@1',
    public_label='订单查询',
)
