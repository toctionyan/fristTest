"""Authoritative vertical definition for `prepare_refund_from_eligibility`."""
from __future__ import annotations
from typing import Any

from agent_core.kernel.capability import (
    CapabilityAuthorizationContract,
    CapabilityCompletionContract,
    CapabilityIdempotencyContract,
    CapabilityInputContract,
    CapabilityOutputContract,
    CapabilityPlanningContract,
    CapabilityPreconditionContract,
    CapabilityResourceConflictContract,
    CapabilityTargetContract,
)

from .spec import EcommerceCapabilityDefinition
from .schema_common import TARGET_SCHEMA, LOGISTICS_QUERY_SCHEMA, CONSTRAINT_BINDINGS_SCHEMA, function_schema, target_query_schema, draft_schema
from .execution_adapter import _engine, execute_one

def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "prepare_refund_from_eligibility", lambda engine: engine._prepare_refund_from_eligibility(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.refund.eligibility.promote',
    tool_name='prepare_refund_from_eligibility',
    category='application',
    planner_rule='基于唯一有效退款资格生成 Draft。',
    execution_kind='action_draft',
    goal_completion_types=('action',),
    completion_effects=('refund.create:order',),
    discovery_examples=('按这个资格退款', '根据资格申请退款', '用这个资格退款'),
    exclusion_examples=('查询资格', '可以退款吗', '退款进度'),
    schema=function_schema("prepare_refund_from_eligibility", "将唯一有效退款资格转换为 Draft。", {"eligibility_handle": {"type": "string"}, "action_span": {"type": "string"}}, ["eligibility_handle", "action_span"]),
    executor=execute,
    presentation_contract='commerce.next_actions@1',
    contract_version='2',
    planning_contract=CapabilityPlanningContract(
        target=CapabilityTargetContract(
            resource_types=('order',),
            cardinality='exactly_one',
            binding_sources=('capability_output',),
        ),
        requires=(
            CapabilityInputContract(
                name='eligibility_assessment',
                type_name='RefundEligibilityAssessment',
                source_types=('capability_output',),
                authority='verified_ledger',
                freshness_seconds=300,
            ),
            CapabilityInputContract(
                name='action_evidence',
                type_name='CurrentTurnActionEvidence',
                source_types=('user_input',),
                authority='literal_evidence',
            ),
        ),
        produces=(
            CapabilityOutputContract(
                name='refund_draft',
                type_name='TransactionDraft',
                authority='transaction_authority',
                completion_proof=False,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code='eligibility_is_current',
                description='退款资格必须存在、属于当前作用域且未过期。',
                verifier_owner='verified_ledger',
            ),
            CapabilityPreconditionContract(
                code='eligibility_matches_refund',
                description='资格核验必须属于 create_refund 动作。',
                verifier_owner='verified_ledger',
            ),
            CapabilityPreconditionContract(
                code='no_conflicting_interaction',
                description='当前会话不能存在冲突的待处理写交互。',
                verifier_owner='transaction_authority',
            ),
        ),
        authorization=CapabilityAuthorizationContract(
            required=True,
            mode='structured_interaction',
            authority='transaction_authority',
        ),
        completion=CapabilityCompletionContract(
            mode='transaction_receipt',
            proof_type='RefundReceipt',
            proof_source='transaction_authority',
        ),
        idempotency=CapabilityIdempotencyContract(
            required=True,
            scope_fields=('target_binding', 'operation'),
            authority='transaction_authority',
        ),
        resource_conflict=CapabilityResourceConflictContract(
            mode='serialize_by_key',
            key_fields=('order_id',),
            authority='transaction_authority',
        ),
    ),
    public_label=None,
)
