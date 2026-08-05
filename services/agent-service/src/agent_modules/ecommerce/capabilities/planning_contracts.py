from __future__ import annotations

"""Module-owned Capability Contract v2 builders for ecommerce verticals.

These helpers remove repetitive dataclass wiring without moving business
semantics into the Kernel.  Every capability file still declares its concrete
resource, typed inputs, proof type and authority.
"""

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

_ORDER_SOURCES = ("target_resolver", "visible_result_ref", "verified_context")


def verified_read_contract(
    *,
    resource_types: tuple[str, ...],
    cardinality: str,
    target_type: str,
    output_name: str,
    output_type: str,
    proof_source: str = "business_service",
    output_authority: str | None = None,
    target_sources: tuple[str, ...] = _ORDER_SOURCES,
    extra_inputs: tuple[CapabilityInputContract, ...] = (),
    extra_preconditions: tuple[CapabilityPreconditionContract, ...] = (),
    freshness_seconds: int | None = 60,
) -> CapabilityPlanningContract:
    return CapabilityPlanningContract(
        target=CapabilityTargetContract(
            resource_types=resource_types,
            cardinality=cardinality,
            binding_sources=target_sources,
        ),
        requires=(
            CapabilityInputContract(
                name="target_binding",
                type_name=target_type,
                source_types=target_sources,
                authority="authoritative",
            ),
            *extra_inputs,
        ),
        produces=(
            CapabilityOutputContract(
                name=output_name,
                type_name=output_type,
                authority=output_authority or proof_source,
                completion_proof=True,
                freshness_seconds=freshness_seconds,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code="target_binding_verified",
                description="目标范围必须来自 TargetResolver、已验证可见结果或可信上下文。",
                verifier_owner="target_resolver",
            ),
            CapabilityPreconditionContract(
                code="target_scope_authorized",
                description="查询对象必须属于当前租户和业务主体。",
                verifier_owner="business_service",
            ),
            *extra_preconditions,
        ),
        completion=CapabilityCompletionContract(
            mode="tool_output",
            proof_type=output_type,
            proof_source=proof_source,
            output_name=output_name,
        ),
    )


def policy_consult_contract(*, policy_type: str, output_type: str) -> CapabilityPlanningContract:
    return verified_read_contract(
        resource_types=("order",),
        cardinality="one_or_collection",
        target_type="ResolvedOrderPolicyTarget",
        output_name=f"{policy_type}_policy_advice",
        output_type=output_type,
        proof_source="policy_knowledge_authority",
        output_authority="policy_knowledge_authority",
        extra_inputs=(
            CapabilityInputContract(
                name="policy_question",
                type_name="PolicyQuestion",
                source_types=("user_input", "verified_context"),
                required=False,
                authority="candidate",
            ),
        ),
        extra_preconditions=(
            CapabilityPreconditionContract(
                code=f"{policy_type}_policy_scope_exact",
                description="只能查询当前能力声明的精确政策域。",
                verifier_owner="policy_retrieval_runtime",
            ),
        ),
        freshness_seconds=300,
    )


def action_draft_contract(
    *,
    operation: str,
    receipt_type: str,
    extra_inputs: tuple[CapabilityInputContract, ...] = (),
) -> CapabilityPlanningContract:
    return CapabilityPlanningContract(
        target=CapabilityTargetContract(
            resource_types=("order",),
            cardinality="exactly_one",
            binding_sources=_ORDER_SOURCES,
        ),
        requires=(
            CapabilityInputContract(
                name="target_binding",
                type_name="ResolvedOrderBinding",
                source_types=_ORDER_SOURCES,
                authority="authoritative",
            ),
            *extra_inputs,
        ),
        produces=(
            CapabilityOutputContract(
                name=f"{operation}_draft",
                type_name="TransactionDraft",
                authority="transaction_authority",
                completion_proof=False,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code="exactly_one_order",
                description="业务申请必须绑定唯一订单。",
                verifier_owner="operation_preparation_runtime",
            ),
            CapabilityPreconditionContract(
                code=f"{operation}_operation_supported",
                description="目标订单必须支持该业务动作。",
                verifier_owner="business_service",
            ),
            CapabilityPreconditionContract(
                code="no_conflicting_interaction",
                description="当前会话不能存在冲突的待处理写交互。",
                verifier_owner="transaction_authority",
            ),
        ),
        authorization=CapabilityAuthorizationContract(
            required=True,
            mode="structured_interaction",
            authority="transaction_authority",
        ),
        completion=CapabilityCompletionContract(
            mode="transaction_receipt",
            proof_type=receipt_type,
            proof_source="transaction_authority",
        ),
        idempotency=CapabilityIdempotencyContract(
            required=True,
            scope_fields=("target_binding", "operation"),
            authority="transaction_authority",
        ),
        resource_conflict=CapabilityResourceConflictContract(
            mode="serialize_by_key",
            key_fields=("order_id",),
            authority="transaction_authority",
        ),
    )


def scoped_runtime_read_contract(
    *,
    output_name: str,
    output_type: str,
    proof_source: str = "verified_ledger",
    extra_inputs: tuple[CapabilityInputContract, ...] = (),
) -> CapabilityPlanningContract:
    return CapabilityPlanningContract(
        target=CapabilityTargetContract(resource_types=(), cardinality="none", binding_sources=("verified_context",)),
        requires=(
            CapabilityInputContract(
                name="conversation_scope",
                type_name="VerifiedConversationScope",
                source_types=("verified_context",),
                authority="authoritative",
            ),
            *extra_inputs,
        ),
        produces=(
            CapabilityOutputContract(
                name=output_name,
                type_name=output_type,
                authority=proof_source,
                completion_proof=True,
                freshness_seconds=60,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code="conversation_scope_verified",
                description="运行态查询必须绑定当前租户、用户和会话。",
                verifier_owner="runtime_scope",
            ),
        ),
        completion=CapabilityCompletionContract(
            mode="tool_output",
            proof_type=output_type,
            proof_source=proof_source,
            output_name=output_name,
        ),
    )


def session_correction_contract(
    *,
    resource_type: str,
    input_name: str,
    input_type: str,
    output_type: str,
) -> CapabilityPlanningContract:
    sources = ("visible_result_ref", "verified_context")
    return CapabilityPlanningContract(
        target=CapabilityTargetContract(
            resource_types=(resource_type,),
            cardinality="exactly_one",
            binding_sources=sources,
        ),
        requires=(
            CapabilityInputContract(
                name=input_name,
                type_name=input_type,
                source_types=sources,
                authority="authoritative",
            ),
            CapabilityInputContract(
                name="current_turn_stop_evidence",
                type_name="CurrentTurnActionEvidence",
                source_types=("user_input",),
                authority="literal_evidence",
            ),
        ),
        produces=(
            CapabilityOutputContract(
                name="session_correction",
                type_name=output_type,
                authority="runtime_state_authority",
                completion_proof=True,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code="runtime_handle_verified",
                description="只能修改当前作用域内已验证且仍有效的运行态对象。",
                verifier_owner="runtime_state_authority",
            ),
        ),
        completion=CapabilityCompletionContract(
            mode="tool_output",
            proof_type=output_type,
            proof_source="runtime_state_authority",
            output_name="session_correction",
        ),
    )


def unsupported_report_contract() -> CapabilityPlanningContract:
    return CapabilityPlanningContract(
        target=CapabilityTargetContract(resource_types=(), cardinality="none", binding_sources=("capability_surface",)),
        requires=(
            CapabilityInputContract(
                name="unsupported_goal_binding",
                type_name="ExactUnsupportedGoalBinding",
                source_types=("capability_surface",),
                authority="authoritative",
            ),
            CapabilityInputContract(
                name="request_evidence",
                type_name="CurrentTurnRequestEvidence",
                source_types=("user_input",),
                authority="literal_evidence",
            ),
        ),
        produces=(
            CapabilityOutputContract(
                name="unsupported_report",
                type_name="UnsupportedCapabilityReport",
                authority="capability_surface",
                completion_proof=True,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code="exact_capability_absence_proven",
                description="只有精确效果能力缺失已由 Capability Surface 证明时才能报告不支持。",
                verifier_owner="capability_surface",
            ),
        ),
        completion=CapabilityCompletionContract(
            mode="unsupported_report",
            proof_type="UnsupportedCapabilityReport",
            proof_source="capability_surface",
        ),
    )


def clarification_contract() -> CapabilityPlanningContract:
    return CapabilityPlanningContract(
        target=CapabilityTargetContract(resource_types=(), cardinality="none", binding_sources=("verified_context",)),
        requires=(
            CapabilityInputContract(
                name="candidate_set",
                type_name="VerifiedClarificationCandidates",
                source_types=("verified_context", "visible_result_ref"),
                authority="authoritative",
            ),
            CapabilityInputContract(
                name="reference_evidence",
                type_name="CurrentTurnReferenceEvidence",
                source_types=("user_input",),
                authority="literal_evidence",
            ),
        ),
        produces=(
            CapabilityOutputContract(
                name="clarification_request",
                type_name="VerifiedClarificationRequest",
                authority="goal_blocker_runtime",
                completion_proof=True,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code="multiple_real_candidates_verified",
                description="澄清必须基于多个真实且可见的候选，不能制造歧义。",
                verifier_owner="goal_blocker_runtime",
            ),
        ),
        completion=CapabilityCompletionContract(
            mode="tool_output",
            proof_type="VerifiedClarificationRequest",
            proof_source="goal_blocker_runtime",
            output_name="clarification_request",
        ),
    )
