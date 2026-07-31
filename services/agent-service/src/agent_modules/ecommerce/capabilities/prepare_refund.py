"""Authoritative vertical definition for `prepare_refund`."""
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
    return execute_one(state, "prepare_refund", lambda engine: engine.execute_prepare_refund(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.refund.prepare',
    tool_name='prepare_refund',
    category='application',
    planner_rule='为退款申请生成 Draft，绝不直接写业务。',
    execution_kind='action_draft',
    goal_completion_types=('action',),
    completion_effects=('refund.create:order',),
    discovery_examples=('帮我退款', '申请退款', '办理退款', '给我退款', '提交退款', '准备退款', '退款草稿', '先准备退款', '都退了', '退了吧'),
    exclusion_examples=('可以退款吗', '能退吗', '退款资格', '退款进度', '退款规则'),
    schema=draft_schema("prepare_refund", "生成退款申请 Draft，不直接写业务。"),
    executor=execute,
    presentation_contract='commerce.next_actions@1',
    contract_version='2',
    planning_contract=CapabilityPlanningContract(
        target=CapabilityTargetContract(
            resource_types=('order',),
            cardinality='exactly_one',
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
                type_name='RefundReason',
                source_types=('user_input', 'structured_interaction'),
                authority='candidate_then_structured',
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
                code='exactly_one_order',
                description='退款申请必须绑定唯一订单。',
                verifier_owner='operation_preparation_runtime',
            ),
            CapabilityPreconditionContract(
                code='refund_operation_supported',
                description='当前订单必须支持退款申请动作。',
                verifier_owner='business_service',
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
    public_label='退款申请',
)
