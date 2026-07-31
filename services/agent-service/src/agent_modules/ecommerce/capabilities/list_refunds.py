"""Authoritative vertical definition for `list_refunds`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "list_refunds", lambda engine: engine.execute_list_refunds(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.refunds.list',
    tool_name='list_refunds',
    category='query',
    planner_rule='查询一个或一组订单/退款记录的退款进度；用户要求全部时使用 collection。',
    execution_kind='grounding_read',
    goal_completion_types=('query',),
    completion_effects=('refund.query_status:refund',),
    discovery_examples=('退款进度', '退款状态', '退款到账', '多久到账', '退款什么时候', '退款记录'),
    exclusion_examples=('可以退款吗', '能退吗', '退款资格', '退款政策', '帮我退款', '申请退款'),
    schema=target_query_schema("list_refunds", "查询一个或一组订单/退款记录的退款办理进度。", shape=("one", "collection")),
    executor=execute,
    presentation_contract='commerce.business_status_list@1',
    public_label=None,
)
