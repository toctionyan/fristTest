"""Authoritative vertical definition for `get_order_logistics`."""
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
    return execute_one(state, "get_order_logistics", lambda engine: engine.execute_get_order_logistics(state, dict(args or {})))

DEFINITION = EcommerceCapabilityDefinition(
    key='ecommerce.order.logistics',
    tool_name='get_order_logistics',
    category='query',
    planner_rule=(
        '查询已验证订单集合的物流；精确物流状态只放 query.delivery_status，“已发出/还没发出”'
        '这类发货阶段判断放 query.dispatched，并为该条件提供 '
        'constraint_bindings。target 只表达查询范围：全量用 all_orders，单个历史可见集合用 '
        'collection+left_handle；需要组合多个已验证可见范围时可使用 set_operation 的 union/intersection/difference，'
        '但不要用 target.set_operation/filter 重复表达物流状态条件。物流术语中“在路上/在途”'
        '严格对应运输中，不包含尚未离开商家的待发货。'
    ),
    execution_kind='grounding_read',
    goal_completion_types=('query',),
    completion_effects=('order.query_logistics:order',),
    discovery_examples=('物流', '在路上', '在途', '运输中', '派送中', '什么时候发货', '发货了吗', '到哪了'),
    exclusion_examples=('退款进度', '售后进度', '发票进度', '退款规则', '申请退款'),
    schema=function_schema(
        "get_order_logistics",
        (
            "查询已验证订单集合的物流。精确物流状态筛选写入 query.delivery_status；‘已发出/已经发货’"
            "写入 query.dispatched=true，‘尚未发出/还没发货’写入 query.dispatched=false；二者不能同时出现，"
            "且必须用 constraint_bindings 绑定对应 query 参数；target 只表达范围：可用 all_orders、仅含 "
            "mode+left_handle 的 collection，或用 set_operation 的 union/intersection/difference 组合多个已验证"
            "可见范围。不要把 operator/status 放进 collection，也不要用 set_operation/filter 代替物流 query。"
            "物流术语中“在路上/在途”严格对应 delivery_status=运输中；待发货表示尚未离开商家，"
            "不能算作在路上。"
        ),
        {
            "target": TARGET_SCHEMA,
            "expected_shape": {"type": "string", "enum": ["one", "collection"]},
            "reference_span": {"type": "string"},
            "query": LOGISTICS_QUERY_SCHEMA,
            "constraint_bindings": CONSTRAINT_BINDINGS_SCHEMA,
        },
        ["target", "expected_shape", "reference_span"],
    ),
    executor=execute,
    presentation_contract='commerce.logistics_overview@1',
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
                type_name='ResolvedOrderSet',
                source_types=('target_resolver', 'visible_result_ref', 'verified_context'),
                authority='authoritative',
            ),
            CapabilityInputContract(
                name='logistics_query',
                type_name='LogisticsQuery',
                source_types=('user_input', 'verified_context'),
                required=False,
                authority='candidate',
            ),
        ),
        produces=(
            CapabilityOutputContract(
                name='logistics_snapshot',
                type_name='VerifiedLogisticsSnapshot',
                authority='business_service',
                completion_proof=True,
                freshness_seconds=60,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code='target_binding_verified',
                description='查询范围必须由 TargetResolver 或已验证可见结果绑定。',
                verifier_owner='target_resolver',
            ),
            CapabilityPreconditionContract(
                code='target_owner_verified',
                description='订单必须属于当前租户和用户。',
                verifier_owner='business_service',
            ),
        ),
        completion=CapabilityCompletionContract(
            mode='tool_output',
            proof_type='VerifiedLogisticsSnapshot',
            proof_source='business_service',
            output_name='logistics_snapshot',
        ),
    ),
    public_label='物流查询',
)
