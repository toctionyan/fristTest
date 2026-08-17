"""Authoritative vertical definition for `evaluate_refund_eligibility`."""
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
    return execute_one(state, "evaluate_refund_eligibility", lambda engine: engine.execute_evaluate_refund_eligibility(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.refund.eligibility',
    tool_name='evaluate_refund_eligibility',
    category='eligibility',
    planner_rule='只读核验具体订单或已验证订单集合当前能否退款/是否有退款资格，不创建草稿；集合目标必须逐笔核验且不得扩大范围。即使用户说先不要提交，也应使用本能力给出资格结论。',
    execution_kind='grounding_read',
    goal_completion_types=('query', 'consult'),
    completion_effects=('refund.assess_eligibility:order',),
    support_effects=('refund.create:order',),
    discovery_examples=('可以退货退款吗', '可以退款吗', '能退款吗', '能不能退款', '能退吗', '退款资格', '还能退', '它现在能退吗', '它可以退货退款吗'),
    exclusion_examples=('退款进度', '退款状态', '到账', '什么时候到账', '退款规则', '退款政策', '帮我退款', '申请退款', '换货', '换新', '退换货'),
    schema=function_schema("evaluate_refund_eligibility", "只读核验具体订单或已验证订单集合当前能否退款/是否有退款资格，不创建草稿、不提交申请；集合目标逐笔核验，不得收窄成任意单笔或扩大到集合外订单。‘能不能退/可以退款吗/先不要提交’这类问题应使用本能力，而不是泛政策咨询。", {"target": TARGET_SCHEMA, "reference_span": {"type": "string"}, "reason_span": {"type": "string"}, "reason_code": {"type": "string"}, "reason_code_span": {"type": "string"}, "question_span": {"type": "string"}}, ["target", "reference_span", "reason_span", "question_span"]),
    executor=execute,
    presentation_contract='commerce.eligibility_decision@1',
    contract_version='2',
    planning_contract=CapabilityPlanningContract(
        target=CapabilityTargetContract(
            resource_types=('order',),
            cardinality='one_or_collection',
            binding_sources=('target_resolver', 'visible_result_ref', 'verified_context'),
        ),
        requires=(
            CapabilityInputContract(
                name='target_binding',
                type_name='ResolvedOrderBinding',
                source_types=('target_resolver', 'visible_result_ref', 'verified_context'),
                authority='authoritative',
            ),
            CapabilityInputContract(
                name='refund_reason',
                type_name='RefundReasonCandidate',
                source_types=('user_input', 'structured_interaction'),
                required=False,
                authority='candidate',
            ),
        ),
        produces=(
            CapabilityOutputContract(
                name='eligibility_assessment',
                type_name='RefundEligibilityAssessment',
                authority='verified_ledger',
                completion_proof=True,
                freshness_seconds=300,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code='one_or_collection_order_scope',
                description='退款资格核验必须绑定至少一个明确订单；集合目标逐笔核验且不得改变集合成员。',
                verifier_owner='target_resolver',
            ),
            CapabilityPreconditionContract(
                code='target_owner_verified',
                description='订单必须属于当前租户和用户。',
                verifier_owner='business_service',
            ),
            CapabilityPreconditionContract(
                code='refund_operation_registered',
                description='退款业务操作必须在当前模块注册。',
                verifier_owner='operation_registry',
            ),
        ),
        completion=CapabilityCompletionContract(
            mode='tool_output',
            proof_type='RefundEligibilityAssessment',
            proof_source='verified_ledger',
            output_name='eligibility_assessment',
        ),
    ),
    public_label=None,
)
