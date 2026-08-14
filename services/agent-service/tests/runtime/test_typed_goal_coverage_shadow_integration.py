from __future__ import annotations

from agent_core.goal_graph.compiler import compile_frozen_semantic_contract
from agent_core.kernel.capability import (
    CapabilityAuthorizationContract,
    CapabilityCompletionContract,
    CapabilityInputContract,
    CapabilityOutputContract,
    CapabilityPlanningContract,
    CapabilityPreconditionContract,
    CapabilityTargetContract,
    ToolCapabilityContract,
)
from agent_core.kernel.capability_registry import CapabilityBinding, CapabilityRegistry
from agent_core.lifecycle.goal_capability_coverage import build_goal_capability_coverage
from agent_core.lifecycle.pretool_planner import build_pretool_shadow_plan
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract


_SCOPE = {
    "tenant_id": "tenant-1",
    "user_id": "u001",
    "thread_id": "web-u001-shadow",
}


def _binding(
    *,
    tool_name: str,
    effect: str,
    target_cardinality: str = "none",
) -> CapabilityBinding:
    target = (
        CapabilityTargetContract(resource_types=(), cardinality="none")
        if target_cardinality == "none"
        else CapabilityTargetContract(
            resource_types=("order",),
            cardinality=target_cardinality,
            binding_sources=("target_resolver",),
        )
    )
    requires = (
        (
            CapabilityInputContract(
                name="target_binding",
                type_name="ResolvedOrderBinding",
                source_types=("target_resolver",),
                authority="authoritative",
            ),
        )
        if target_cardinality != "none"
        else (
            CapabilityInputContract(
                name="request_evidence",
                type_name="UserRequestEvidence",
                source_types=("user_input",),
                authority="candidate",
            ),
        )
    )
    planning = CapabilityPlanningContract(
        target=target,
        requires=requires,
        produces=(
            CapabilityOutputContract(
                name="result",
                type_name="VerifiedResult",
                completion_proof=True,
            ),
        ),
        preconditions=(
            CapabilityPreconditionContract(
                code="scope_valid",
                description="scope is valid",
                verifier_owner="runtime",
            ),
        ),
        authorization=CapabilityAuthorizationContract(),
        completion=CapabilityCompletionContract(
            mode="tool_output",
            proof_type="VerifiedResult",
            output_name="result",
        ),
    )
    contract = ToolCapabilityContract(
        key=f"test.{tool_name}",
        tool_name=tool_name,
        category="query",
        writes_business_data=False,
        evidence_sources=("business_service",),
        planner_rule="typed shadow test capability",
        unavailable_response="unavailable",
        execution_kind="grounding_read",
        completion_effects=(effect,),
        contract_version="2",
        planning_contract=planning,
    )
    return CapabilityBinding(
        domain_id="test",
        contract=contract,
        schema={
            "type": "function",
            "function": {
                "name": tool_name,
                "parameters": {"type": "object"},
            },
        },
        dispatcher=lambda *args, **kwargs: {},
        public_label=tool_name,
    )


def _registry(*bindings: CapabilityBinding) -> CapabilityRegistry:
    return CapabilityRegistry(bindings)


def _freeze(goals: list[dict]) -> dict:
    user_text = "；".join(str(row["evidence_span"]) for row in goals)
    return freeze_semantic_contract(
        turn=1,
        user_text=user_text,
        summary="typed shadow integration",
        goals=goals,
        alignment_proof={
            "verdict": "exact",
            "source": "test",
            "independent": True,
        },
    )


def _goal(
    *,
    goal_id: str,
    operation: str,
    evidence_span: str,
    depends_on: list[str] | None = None,
    target_candidate: dict | None = None,
    cardinality: str = "none",
) -> dict:
    row = {
        "goal_id": goal_id,
        "description": evidence_span,
        "evidence_span": evidence_span,
        "requested_effect": {
            "domain": "order",
            "operation": operation,
            "object_type": "order",
        },
        "expected_result_cardinality": cardinality,
        "required": True,
        "depends_on": list(depends_on or []),
    }
    if target_candidate is not None:
        row["target_candidate"] = dict(target_candidate)
    return row


def test_no_typed_graph_preserves_legacy_payload_shape() -> None:
    contract = _freeze(
        [_goal(goal_id="g1", operation="query_summary", evidence_span="查询摘要")]
    )
    registry = _registry(
        _binding(
            tool_name="query_summary",
            effect="order.query_summary:order",
        )
    )

    first = build_goal_capability_coverage(
        goals=contract["goals"],
        goal_plans=[],
        capability_registry=registry,
    )
    second = build_goal_capability_coverage(
        goals=contract["goals"],
        goal_plans=[],
        capability_registry=registry,
    )

    assert first == second
    assert "typed_goal_capability_shadow" not in first
    assert "typed_shadow_comparison" not in first


def test_legacy_exact_effect_does_not_hide_unresolved_typed_target() -> None:
    contract = _freeze(
        [
            _goal(
                goal_id="g1",
                operation="query_details",
                evidence_span="查询订单详情",
                target_candidate={"order_id": "10002"},
                cardinality="single",
            )
        ]
    )
    registry = _registry(
        _binding(
            tool_name="query_details",
            effect="order.query_details:order",
            target_cardinality="exactly_one",
        )
    )
    graph = compile_frozen_semantic_contract(contract, scope=_SCOPE)

    coverage = build_goal_capability_coverage(
        goals=contract["goals"],
        goal_plans=[],
        capability_registry=registry,
        typed_goal_graph=graph,
        frozen_contract=contract,
    )

    assert coverage["coverage_status"] == "COMPLETE"
    assert coverage["goals"][0]["completion_tools"] == ["query_details"]
    assert coverage["typed_goal_capability_shadow"]["coverage_status"] == "DATAFLOW_OPEN"
    assert coverage["typed_goal_capability_shadow"]["goals"][0]["closed_capability_tools"] == []
    assert coverage["typed_shadow_comparison"]["status"] == "DIVERGED"
    assert any(
        row["code"] == "LEGACY_COVERED_TYPED_UNCLOSED"
        and row["goal_id"] == "g1"
        for row in coverage["typed_shadow_comparison"]["divergences"]
    )
    assert coverage["must_not_dispatch"] is True
    assert coverage["creates_permit"] is False


def test_typed_shadow_exact_closure_matches_without_changing_legacy_selection() -> None:
    contract = _freeze(
        [_goal(goal_id="g1", operation="query_summary", evidence_span="查询摘要")]
    )
    registry = _registry(
        _binding(
            tool_name="query_summary",
            effect="order.query_summary:order",
        )
    )
    graph = compile_frozen_semantic_contract(contract, scope=_SCOPE)

    coverage = build_goal_capability_coverage(
        goals=contract["goals"],
        goal_plans=[],
        capability_registry=registry,
        typed_goal_graph=graph,
        frozen_contract=contract,
    )

    typed = coverage["typed_goal_capability_shadow"]
    comparison = coverage["typed_shadow_comparison"]
    assert typed["coverage_status"] == "COMPLETE"
    assert typed["interaction_goal_ids"] == ["g1"]
    assert typed["goals"][0]["closed_capability_tools"] == ["query_summary"]
    assert comparison["status"] == "MATCHED"
    assert comparison["divergences"] == []
    assert comparison["typed_shadow_may_widen_legacy_coverage"] is False
    assert comparison["blocks_execution"] is False


def test_typed_shadow_exposes_unverified_legacy_dependency_claim() -> None:
    contract = _freeze(
        [
            _goal(
                goal_id="g1",
                operation="query_summary",
                evidence_span="先查询摘要",
            ),
            _goal(
                goal_id="g2",
                operation="query_status",
                evidence_span="再根据它查询状态",
                depends_on=["g1"],
            ),
        ]
    )
    registry = _registry(
        _binding(
            tool_name="query_summary",
            effect="order.query_summary:order",
        ),
        _binding(
            tool_name="query_status",
            effect="order.query_status:order",
        ),
    )
    graph = compile_frozen_semantic_contract(contract, scope=_SCOPE)

    coverage = build_goal_capability_coverage(
        goals=contract["goals"],
        goal_plans=[],
        capability_registry=registry,
        typed_goal_graph=graph,
        frozen_contract=contract,
    )

    assert coverage["coverage_status"] == "COMPLETE"
    assert coverage["typed_goal_capability_shadow"]["coverage_status"] == "DATAFLOW_OPEN"
    assert coverage["typed_goal_capability_shadow"]["derived_dependencies"]["g2"] == []
    assert any(
        row["code"] == "LEGACY_DEPENDENCY_DIFFERS_FROM_VERIFIED_DATAFLOW"
        and row["goal_id"] == "g2"
        and row["legacy_depends_on"] == ["g1"]
        and row["typed_derived_dependencies"] == []
        for row in coverage["typed_shadow_comparison"]["divergences"]
    )


def test_invalid_scope_is_audit_only_and_never_changes_legacy_coverage() -> None:
    contract = _freeze(
        [_goal(goal_id="g1", operation="query_summary", evidence_span="查询摘要")]
    )
    registry = _registry(
        _binding(
            tool_name="query_summary",
            effect="order.query_summary:order",
        )
    )
    graph = compile_frozen_semantic_contract(
        contract,
        scope={"tenant_id": "", "user_id": "u001", "thread_id": "t1"},
    )

    coverage = build_goal_capability_coverage(
        goals=contract["goals"],
        goal_plans=[],
        capability_registry=registry,
        typed_goal_graph=graph,
        frozen_contract=contract,
    )

    assert coverage["coverage_status"] == "COMPLETE"
    assert coverage["typed_goal_capability_shadow"]["coverage_status"] == "STRUCTURAL_INVALID"
    assert any(
        row["code"] == "TYPED_GOAL_GRAPH_STRUCTURAL_INVALID"
        for row in coverage["typed_shadow_comparison"]["divergences"]
    )
    assert coverage["must_not_dispatch"] is True


def test_pretool_shadow_plan_compiles_scoped_goal_graph_and_attaches_comparison() -> None:
    contract = _freeze(
        [_goal(goal_id="g1", operation="query_summary", evidence_span="查询摘要")]
    )
    registry = _registry(
        _binding(
            tool_name="query_summary",
            effect="order.query_summary:order",
        )
    )

    plan = build_pretool_shadow_plan(
        state={
            "frozen_semantic_contract": contract,
            "current_tenant_id": _SCOPE["tenant_id"],
            "current_user_id": _SCOPE["user_id"],
            "current_thread_id": _SCOPE["thread_id"],
        },
        capability_registry=registry,
    )

    assert plan["typed_goal_graph"]["scope"] == _SCOPE
    coverage = plan["global_goal_capability_coverage"]
    assert coverage["typed_goal_capability_shadow"]["graph_digest"] == plan["typed_goal_graph"]["graph_digest"]
    assert coverage["typed_shadow_comparison"]["status"] == "MATCHED"
    assert plan["must_not_dispatch"] is True
    assert plan["creates_permit"] is False
    assert plan["execution_readiness"] == "not_evaluated_shadow"
