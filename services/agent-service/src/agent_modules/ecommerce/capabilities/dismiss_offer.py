"""Authoritative vertical definition for `dismiss_offer`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "dismiss_offer", lambda engine: engine._dismiss_offer(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='runtime.offer.dismiss',
    tool_name='dismiss_offer',
    category='correction',
    planner_rule='撤销或停止当前未执行 Draft；这是聊天中明确停止办理的唯一安全路径，不提交业务申请。',
    execution_kind='session_correction',
    goal_completion_types=('action',),
    completion_effects=('transaction.cancel_draft:transaction_draft',),
    discovery_examples=('取消草稿', '撤销申请草稿', '先不办理', '不办了', '放弃办理', '停止办理', '停止这个办理', '不要提交了'),
    exclusion_examples=('取消订单', '退款申请'),
    schema=function_schema("dismiss_offer", "撤销或停止当前未执行 Draft；offer_handle 必须来自当前已验证办理状态，reference_span 必须来自本轮停止原话。", {"offer_handle": {"type": "string"}, "reference_span": {"type": "string"}}, ["offer_handle", "reference_span"]),
    executor=execute,
    presentation_contract='runtime.transaction_status@1',
    public_label=None,
)
