"""Authoritative vertical definition for `ask_context_clarification`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "ask_context_clarification", lambda engine: engine._ask_context_clarification(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='runtime.context.clarify',
    tool_name='ask_context_clarification',
    category='clarification',
    planner_rule='在多个真实可见结果候选间澄清。',
    execution_kind='clarification_read',
    goal_completion_types=('clarification',),
    discovery_examples=('需要选择订单', '多个候选', '请明确对象'),
    schema=function_schema("ask_context_clarification", "在真实候选间澄清。", {"reference_span": {"type": "string"}, "candidate_handles": {"type": "array", "items": {"type": "string"}}}, ["reference_span", "candidate_handles"]),
    executor=execute,
    presentation_contract='runtime.transaction_status@1',
    public_label=None,
)
