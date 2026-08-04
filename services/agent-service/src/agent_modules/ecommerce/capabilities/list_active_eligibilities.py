"""Authoritative vertical definition for `list_active_eligibilities`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .planning_contracts import scoped_runtime_read_contract
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "list_active_eligibilities", lambda engine: engine._list_active_eligibilities(state))

DEFINITION = EcommerceCapabilityDefinition(
    key='runtime.eligibility.list',
    tool_name='list_active_eligibilities',
    category='query',
    planner_rule='查看已有资格核验。',
    execution_kind='grounding_read',
    goal_completion_types=('query',),
    completion_effects=('refund.list_eligibilities:refund_eligibility',),
    support_effects=('refund.create:order',),
    discovery_examples=('已有资格', '资格记录', '之前的资格', '退款资格记录'),
    exclusion_examples=('可以退款吗', '能退吗', '帮我退款'),
    schema=function_schema("list_active_eligibilities", "查看已有资格核验。", {}, []),
    executor=execute,
    contract_version='2',
    planning_contract=scoped_runtime_read_contract(
        output_name="active_eligibilities", output_type="VerifiedRefundEligibilityCollection",
    ),
    presentation_contract='runtime.transaction_status@1',
    public_label=None,
)
