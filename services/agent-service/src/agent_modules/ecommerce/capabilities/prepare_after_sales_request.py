"""Authoritative vertical definition for `prepare_after_sales_request`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "prepare_after_sales_request", lambda engine: engine.execute_prepare_after_sales_request(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.after_sales.prepare',
    tool_name='prepare_after_sales_request',
    category='application',
    planner_rule='为售后申请生成 Draft，绝不直接写业务。',
    execution_kind='action_draft',
    goal_completion_types=('action',),
    completion_effects=('after_sales.create:order',),
    discovery_examples=('申请售后', '申请换货', '申请维修', '退货申请', '帮我换货', '帮我维修'),
    exclusion_examples=('售后进度', '换货进度', '维修进度', '退款申请'),
    schema=draft_schema("prepare_after_sales_request", "生成售后申请 Draft，不直接写业务。", {"service_type": {"type": "string", "enum": ["repair", "exchange", "return", "general"]}, "service_type_span": {"type": "string"}}),
    executor=execute,
    presentation_contract='commerce.next_actions@1',
    public_label='售后申请',
)
