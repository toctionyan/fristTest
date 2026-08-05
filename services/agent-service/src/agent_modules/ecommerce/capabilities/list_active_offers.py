"""Authoritative vertical definition for `list_active_offers`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .planning_contracts import scoped_runtime_read_contract
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "list_active_offers", lambda engine: engine._list_active_offers(state))

DEFINITION = EcommerceCapabilityDefinition(
    key='runtime.offer.list',
    tool_name='list_active_offers',
    category='query',
    planner_rule='查看当前待处理 Draft。',
    execution_kind='grounding_read',
    goal_completion_types=('query',),
    completion_effects=('transaction.list_drafts:transaction_draft',),
    support_effects=('transaction.cancel_draft:transaction_draft', 'transaction.query_status:transaction'),
    discovery_examples=('待处理申请', '待办草稿', '有哪些草稿', '办理草稿', '未提交申请'),
    exclusion_examples=('申请进度', '帮我申请', '提交申请'),
    schema=function_schema("list_active_offers", "查看待处理 Draft。", {}, []),
    executor=execute,
    contract_version='2',
    planning_contract=scoped_runtime_read_contract(
        output_name="active_drafts", output_type="VerifiedTransactionDraftCollection",
        proof_source="transaction_authority",
    ),
    presentation_contract='runtime.transaction_status@1',
    public_label=None,
)
