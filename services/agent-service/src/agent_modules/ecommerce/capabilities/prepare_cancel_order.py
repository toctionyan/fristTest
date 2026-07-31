"""Authoritative vertical definition for `prepare_cancel_order`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "prepare_cancel_order", lambda engine: engine.execute_prepare_cancel_order(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.order.cancel.prepare',
    tool_name='prepare_cancel_order',
    category='application',
    planner_rule='为取消订单生成 Draft，绝不直接写业务。',
    execution_kind='action_draft',
    goal_completion_types=('action',),
    completion_effects=('order.cancel:order',),
    discovery_examples=('取消订单', '帮我取消', '不要这个订单', '把订单取消', '都取消', '取消掉', '取消'),
    exclusion_examples=('能取消吗', '取消规则', '取消进度'),
    schema=draft_schema("prepare_cancel_order", "生成取消订单 Draft，不直接写业务。"),
    executor=execute,
    presentation_contract='commerce.next_actions@1',
    public_label=None,
)
