"""Authoritative vertical definition for `list_invoices`."""
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
    return execute_one(state, "list_invoices", lambda engine: engine.execute_list_invoices(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.invoices.list',
    tool_name='list_invoices',
    category='query',
    planner_rule='查询一个或一组订单/发票记录的办理进度；用户要求全部时使用 collection。',
    execution_kind='grounding_read',
    goal_completion_types=('query',),
    completion_effects=('invoice.query_status:invoice',),
    discovery_examples=('发票进度', '开票进度', '发票状态', '发票记录', '开票记录', '有没有发票', '有发票吗'),
    exclusion_examples=('能开发票吗', '开发票', '帮我开票', '申请发票'),
    schema=target_query_schema("list_invoices", "查询一个或一组订单/发票记录的办理进度。", shape=("one", "collection")),
    executor=execute,
    presentation_contract='commerce.business_status_list@1',
    contract_version='2',
    planning_contract=CapabilityPlanningContract(
        target=CapabilityTargetContract(
            resource_types=('order', 'invoice'),
            cardinality='one_or_collection',
            binding_sources=('target_resolver', 'visible_result_ref', 'verified_context'),
        ),
        requires=(
            CapabilityInputContract(
                name='target_binding',
                type_name='ResolvedInvoiceQueryTarget',
                source_types=('target_resolver', 'visible_result_ref', 'verified_context'),
                authority='authoritative',
            ),
            CapabilityInputContract(
                name='invoice_query',
                type_name='InvoiceStatusQuery',
                source_types=('user_input', 'verified_context'),
                required=False,
                authority='candidate',
            ),
        ),
        produces=(
            CapabilityOutputContract(
                name='invoice_status_snapshot',
                type_name='VerifiedInvoiceStatusSnapshot',
                authority='business_service',
                completion_proof=True,
                freshness_seconds=60,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code='target_binding_verified',
                description='订单或发票范围必须来自已验证绑定。',
                verifier_owner='target_resolver',
            ),
            CapabilityPreconditionContract(
                code='target_owner_verified',
                description='查询对象必须属于当前租户和用户。',
                verifier_owner='business_service',
            ),
        ),
        completion=CapabilityCompletionContract(
            mode='tool_output',
            proof_type='VerifiedInvoiceStatusSnapshot',
            proof_source='business_service',
            output_name='invoice_status_snapshot',
        ),
    ),
    public_label=None,
)
