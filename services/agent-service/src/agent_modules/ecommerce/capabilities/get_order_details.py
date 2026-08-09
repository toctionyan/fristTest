"""Authoritative vertical definition for `get_order_details`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .planning_contracts import verified_read_contract
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "get_order_details", lambda engine: engine.execute_get_order_details(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.order.details',
    tool_name='get_order_details',
    category='query',
    planner_rule='查询一个在调用前已经通过订单号、唯一历史结果或唯一 ResultRef 明确绑定的订单详情；仅当目标已唯一解析时使用 order.query_details:order。用商品名、状态或其他属性寻找订单本身属于 order.list:order，不是详情查询。可作为对该精确订单执行后续动作的前置取证，本身不创建业务动作。',
    execution_kind='grounding_read',
    goal_completion_types=('query',),
    completion_effects=('order.query_details:order',),
    goal_support_types=('consult', 'action'),
    support_effects=('refund.create:order', 'refund.assess_eligibility:order', 'refund.consult_policy:order', 'invoice.create:order', 'invoice.consult_policy:order', 'after_sales.create:order', 'after_sales.consult_policy:order', 'warranty.consult_policy:order', 'order.cancel:order', 'order.query_logistics:order'),
    discovery_examples=('订单详情', '订单状态', '是什么商品', '哪一个订单', '订单信息', '现在是什么状态', '订单10002详情', '刚才那个订单的详情'),
    exclusion_examples=('物流', '在路上', '退款进度', '按商品查订单', '鼠标订单', '某商品的订单', '售后进度', '发票进度', '查一下某商品订单', '键盘订单'),
    schema=target_query_schema("get_order_details", "查询一个已验证订单详情。", shape="one"),
    executor=execute,
    # A read-only detail query must publish the verified order itself.  Reusing
    # the canonical order projector for a one-member result keeps identity and
    # state visible without inventing an unrelated action card.
    contract_version='2',
    planning_contract=verified_read_contract(
        resource_types=("order",), cardinality="exactly_one", target_type="ResolvedOrderBinding",
        output_name="order_details", output_type="VerifiedOrderDetails",
    ),
    presentation_contract='commerce.order_list@1',
    public_label=None,
)
