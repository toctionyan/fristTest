"""Authoritative vertical definition for `query_transaction_lifecycle`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .planning_contracts import scoped_runtime_read_contract
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "query_transaction_lifecycle", lambda engine: engine._query_transaction_lifecycle(state, dict(args or {}), transactions=transactions))

DEFINITION = EcommerceCapabilityDefinition(
    key='runtime.transaction_lifecycle.query',
    tool_name='query_transaction_lifecycle',
    category='transaction_status',
    planner_rule='查询已有 Draft、Attempt 或 Receipt，不创建新业务。',
    execution_kind='grounding_read',
    goal_completion_types=('query',),
    completion_effects=('transaction.query_status:transaction',),
    discovery_examples=('办理到哪', '申请进度', '处理状态', '办理记录', '提交结果', '草稿状态'),
    exclusion_examples=('订单状态', '物流状态', '退款状态', '发票状态', '售后状态'),
    schema=function_schema("query_transaction_lifecycle", "查询已有办理记录。", {"query_span": {"type": "string"}, "transaction_handle": {"type": "string"}}, ["query_span"]),
    executor=execute,
    contract_version='2',
    planning_contract=scoped_runtime_read_contract(
        output_name="transaction_lifecycle", output_type="VerifiedTransactionLifecycleSnapshot",
        proof_source="transaction_authority",
    ),
    presentation_contract='runtime.transaction_status@1',
    public_label=None,
)
