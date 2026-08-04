"""Authoritative vertical definition for `dismiss_eligibility`."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .planning_contracts import session_correction_contract
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "dismiss_eligibility", lambda engine: engine._dismiss_eligibility(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='runtime.eligibility.dismiss',
    tool_name='dismiss_eligibility',
    category='correction',
    planner_rule='停止沿用旧资格核验。',
    execution_kind='session_correction',
    goal_completion_types=('action',),
    completion_effects=('refund.dismiss_eligibility:refund_eligibility',),
    discovery_examples=('别用这个资格', '停止资格核验', '取消资格结果'),
    exclusion_examples=('申请退款', '查询资格'),
    schema=function_schema("dismiss_eligibility", "停止沿用资格核验。", {"eligibility_handle": {"type": "string"}, "reference_span": {"type": "string"}}, ["eligibility_handle", "reference_span"]),
    executor=execute,
    contract_version='2',
    planning_contract=session_correction_contract(
        resource_type="refund_eligibility", input_name="eligibility_binding",
        input_type="VerifiedEligibilityBinding", output_type="EligibilityDismissalOutcome",
    ),
    presentation_contract='runtime.transaction_status@1',
    public_label=None,
)
