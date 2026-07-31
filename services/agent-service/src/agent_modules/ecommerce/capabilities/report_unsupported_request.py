"""Authoritative vertical definition for `report_unsupported_request`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "report_unsupported_request", lambda engine: engine._report_unsupported_request(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='runtime.unsupported',
    tool_name='report_unsupported_request',
    category='unsupported',
    planner_rule='没有精确能力时明确 unsupported，不相似替代。',
    execution_kind='unsupported',
    # The Registry exposes this capability for query/consult/action goals only
    # after discovery found no exact registered candidate.  Surface
    # authorization in CapabilityGate prevents a model from using it to abandon
    # a supported goal.
    goal_completion_types=('query', 'consult', 'action', 'unsupported'),
    discovery_examples=('不支持', '没有这个能力', '无法办理'),
    schema=function_schema("report_unsupported_request", "明确报告未支持请求。", {"request_span": {"type": "string"}}, ["request_span"]),
    executor=execute,
    presentation_contract='runtime.transaction_status@1',
    public_label=None,
)
