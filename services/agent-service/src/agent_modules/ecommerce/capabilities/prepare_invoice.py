"""Authoritative vertical definition for `prepare_invoice`."""
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
    return execute_one(state, "prepare_invoice", lambda engine: engine.execute_prepare_invoice(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.invoice.prepare',
    tool_name='prepare_invoice',
    category='application',
    planner_rule='为开票申请生成 Draft，绝不直接写业务。',
    execution_kind='action_draft',
    goal_completion_types=('action',),
    completion_effects=('invoice.create:order',),
    discovery_examples=('开发票', '开票申请', '帮我开票', '申请发票', '给我开票'),
    exclusion_examples=('能开发票吗', '开票规则', '发票进度', '开票进度'),
    schema=draft_schema("prepare_invoice", "生成开票申请 Draft。", {"invoice_title_span": {"type": "string"}}),
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
                name='invoice_title',
                type_name='InvoiceTitle',
                source_types=('user_input', 'structured_interaction'),
                required=False,
                authority='candidate_then_structured',
            ),
        ),
        produces=(
            CapabilityOutputContract(
                name='invoice_draft',
                type_name='TransactionDraft',
                authority='transaction_authority',
                completion_proof=False,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code='exactly_one_order',
                description='开票申请必须绑定唯一订单。',
                verifier_owner='operation_preparation_runtime',
            ),
            CapabilityPreconditionContract(
                code='invoice_operation_supported',
                description='当前订单必须支持开票申请动作。',
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
            proof_type='InvoiceReceipt',
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
    public_label='开票申请',
)
