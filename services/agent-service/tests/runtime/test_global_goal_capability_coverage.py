from __future__ import annotations

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


def _registry(*, contract_v2: bool = True, writes: bool = False) -> CapabilityRegistry:
    planning = CapabilityPlanningContract(
        target=CapabilityTargetContract(resource_types=("order",), cardinality="exactly_one"),
        requires=(CapabilityInputContract(name="order", type_name="VerifiedOrder", source_types=("target_resolver",)),),
        produces=(
            CapabilityOutputContract(
                name="overview",
                type_name="VerifiedOrderOverview",
                completion_proof=True,
            ),
        ),
        preconditions=(CapabilityPreconditionContract(code="visible", description="visible", verifier_owner="business_service"),),
        completion=CapabilityCompletionContract(
            mode="tool_output",
            proof_type="VerifiedOrderOverview",
            output_name="overview",
        ),
        authorization=CapabilityAuthorizationContract(),
    )
    contract = ToolCapabilityContract(
        key="order.overview",
        tool_name="get_order_overview",
        category="query",
        writes_business_data=writes,
        evidence_sources=("business_service",),
        planner_rule="returns details and logistics",
        unavailable_response="unavailable",
        execution_kind="grounding_read",
        completion_effects=(
            "order.query_details:order",
            "order.query_logistics:order",
        ),
        contract_version="2" if contract_v2 else "1",
        planning_contract=planning if contract_v2 else None,
    )
    binding = CapabilityBinding(
        domain_id="test",
        contract=contract,
        schema={"type": "function", "function": {"name": "get_order_overview", "parameters": {"type": "object"}}},
        dispatcher=lambda *args, **kwargs: {},
        public_label="overview",
    )
    return CapabilityRegistry((binding,))


def test_global_coverage_recognizes_one_capability_for_two_goals() -> None:
    goals = [
        {
            "goal_id": "g1",
            "required": True,
            "requested_effect": {"domain": "order", "operation": "query_details", "object_type": "order"},
            "target_candidate": {"order_id": "10002"},
        },
        {
            "goal_id": "g2",
            "required": True,
            "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
            "target_candidate": {"order_id": "10002"},
        },
    ]
    coverage = build_goal_capability_coverage(
        goals=goals,
        goal_plans=[],
        capability_registry=_registry(),
    )

    assert coverage["coverage_status"] == "COMPLETE"
    assert coverage["uncovered_goal_ids"] == []
    shared = coverage["shared_capability_bindings"]
    assert len(shared) == 1
    assert shared[0]["tool_name"] == "get_order_overview"
    assert shared[0]["goal_ids"] == ["g1", "g2"]
    assert shared[0]["writes_business_data"] is False
    assert shared[0]["coverage_proofs"] == {
        "g1": {
            "requested_effect_identity": "order.query_details:order",
            "output_name": "overview",
            "output_type": "VerifiedOrderOverview",
            "output_authority": "verified_tool_output",
        },
        "g2": {
            "requested_effect_identity": "order.query_logistics:order",
            "output_name": "overview",
            "output_type": "VerifiedOrderOverview",
            "output_authority": "verified_tool_output",
        },
    }
    assert coverage["selected_coverage"]["coverage_id"] == (
        "candidate:coverage:get_order_overview:g1-g2"
    )
    assert coverage["selected_coverage"]["estimated_dispatch_count"] == 1



def test_global_solver_combines_two_safe_shared_capabilities() -> None:
    def composite_binding(
        *,
        key: str,
        tool_name: str,
        resource_type: str,
        output_name: str,
        output_type: str,
        effects: tuple[str, str],
    ) -> CapabilityBinding:
        planning = CapabilityPlanningContract(
            target=CapabilityTargetContract(
                resource_types=(resource_type,),
                cardinality="exactly_one",
            ),
            requires=(
                CapabilityInputContract(
                    name=resource_type,
                    type_name=f"Verified{resource_type.title()}",
                    source_types=("target_resolver",),
                ),
            ),
            produces=(
                CapabilityOutputContract(
                    name=output_name,
                    type_name=output_type,
                    completion_proof=True,
                ),
            ),
            preconditions=(
                CapabilityPreconditionContract(
                    code="visible",
                    description="visible",
                    verifier_owner="business_service",
                ),
            ),
            completion=CapabilityCompletionContract(
                mode="tool_output",
                proof_type=output_type,
                output_name=output_name,
            ),
            authorization=CapabilityAuthorizationContract(),
        )
        contract = ToolCapabilityContract(
            key=key,
            tool_name=tool_name,
            category="query",
            writes_business_data=False,
            evidence_sources=("business_service",),
            planner_rule="composite read",
            unavailable_response="unavailable",
            execution_kind="grounding_read",
            completion_effects=effects,
            contract_version="2",
            planning_contract=planning,
        )
        return CapabilityBinding(
            domain_id="test",
            contract=contract,
            schema={
                "type": "function",
                "function": {"name": tool_name, "parameters": {"type": "object"}},
            },
            dispatcher=lambda *args, **kwargs: {},
            public_label=tool_name,
        )

    registry = CapabilityRegistry(
        (
            composite_binding(
                key="order.overview",
                tool_name="get_order_overview",
                resource_type="order",
                output_name="overview",
                output_type="VerifiedOrderOverview",
                effects=(
                    "order.query_details:order",
                    "order.query_logistics:order",
                ),
            ),
            composite_binding(
                key="invoice.overview",
                tool_name="get_invoice_overview",
                resource_type="invoice",
                output_name="invoice_overview",
                output_type="VerifiedInvoiceOverview",
                effects=(
                    "invoice.query_details:invoice",
                    "invoice.query_status:invoice",
                ),
            ),
        )
    )
    goals = [
        {
            "goal_id": "g1",
            "required": True,
            "requested_effect": {
                "domain": "order",
                "operation": "query_details",
                "object_type": "order",
            },
            "target_candidate": {"order_id": "10002"},
        },
        {
            "goal_id": "g2",
            "required": True,
            "requested_effect": {
                "domain": "order",
                "operation": "query_logistics",
                "object_type": "order",
            },
            "target_candidate": {"order_id": "10002"},
        },
        {
            "goal_id": "g3",
            "required": True,
            "requested_effect": {
                "domain": "invoice",
                "operation": "query_details",
                "object_type": "invoice",
            },
            "target_candidate": {"invoice_id": "INV-1"},
        },
        {
            "goal_id": "g4",
            "required": True,
            "requested_effect": {
                "domain": "invoice",
                "operation": "query_status",
                "object_type": "invoice",
            },
            "target_candidate": {"invoice_id": "INV-1"},
        },
    ]

    coverage = build_goal_capability_coverage(
        goals=goals,
        goal_plans=[],
        capability_registry=registry,
    )

    assert coverage["coverage_status"] == "COMPLETE"
    selected = coverage["selected_coverage"]
    assert selected["strategy"] == "global_safe_shared_set_cover"
    assert selected["covered_goal_ids"] == ["g1", "g2", "g3", "g4"]
    assert selected["estimated_dispatch_count"] == 2
    assert selected["shared_binding_ids"] == [
        "coverage:get_invoice_overview:g3-g4",
        "coverage:get_order_overview:g1-g2",
    ]
    assert selected["independent_goal_ids"] == []


def test_multi_goal_effect_match_requires_primary_completion_proof() -> None:
    from agent_core.lifecycle.semantic_contract import freeze_semantic_contract
    from agent_core.runtime.capability_effects import goal_effect_match_proof

    goals = [
        {
            "goal_id": "g1",
            "description": "查询订单详情",
            "evidence_span": "订单详情",
            "requested_effect": {
                "domain": "order",
                "operation": "query_details",
                "object_type": "order",
            },
            "expected_result_cardinality": "single",
            "required": True,
            "depends_on": [],
        },
        {
            "goal_id": "g2",
            "description": "查询物流状态",
            "evidence_span": "物流状态",
            "requested_effect": {
                "domain": "order",
                "operation": "query_logistics",
                "object_type": "order",
            },
            "expected_result_cardinality": "single",
            "required": True,
            "depends_on": [],
        },
    ]
    contract = freeze_semantic_contract(
        turn=1,
        user_text="订单详情和物流状态",
        summary="查询订单详情和物流状态",
        goals=goals,
        alignment_proof={"verdict": "exact", "source": "test"},
        granularity_proof={"verdict": "exact", "source": "test"},
    )

    proof = goal_effect_match_proof(
        state={"frozen_semantic_contract": contract},
        tool_name="get_order_overview",
        goal_ids=["g1", "g2"],
        registry=_registry(),
    )

    assert proof["allowed"] is True
    assert [row["completion_proof_output"] for row in proof["goals"]] == [
        "overview",
        "overview",
    ]
    assert all(row["multi_goal_completion_proof_required"] for row in proof["goals"])

    no_contract_proof = goal_effect_match_proof(
        state={"frozen_semantic_contract": contract},
        tool_name="get_order_overview",
        goal_ids=["g1", "g2"],
        registry=_registry(contract_v2=False),
    )
    assert no_contract_proof["allowed"] is False
    assert [row["completion_proof_output"] for row in no_contract_proof["goals"]] == [
        None,
        None,
    ]


def test_global_coverage_does_not_claim_an_absent_effect() -> None:
    goals = [
        {
            "goal_id": "g1",
            "required": True,
            "requested_effect": {
                "domain": "order",
                "operation": "query_details",
                "object_type": "order",
            },
        },
        {
            "goal_id": "g2",
            "required": True,
            "requested_effect": {
                "domain": "invoice",
                "operation": "query_status",
                "object_type": "invoice",
            },
        },
    ]
    coverage = build_goal_capability_coverage(
        goals=goals,
        goal_plans=[],
        capability_registry=_registry(),
    )

    assert coverage["coverage_status"] == "INCOMPLETE"
    assert coverage["uncovered_goal_ids"] == ["g2"]
    assert coverage["shared_capability_bindings"] == []


def test_shared_prerequisite_must_come_from_contract_closed_preferred_paths() -> None:
    goal_plans = [
        {
            "goal_id": "g1",
            "preferred_path": {
                "status": "closed",
                "steps": [
                    {
                        "step_id": "producer:g1",
                        "tool_name": "query_order",
                        "input_bindings": [],
                    },
                    {
                        "step_id": "consumer:g1",
                        "tool_name": "details",
                        "input_bindings": [
                            {
                                "binding_kind": "step_output",
                                "producer_tool": "query_order",
                                "producer_step_id": "producer:g1",
                                "type_name": "VerifiedOrder",
                            }
                        ],
                    },
                ],
            },
        },
        {
            "goal_id": "g2",
            "preferred_path": {
                "status": "closed",
                "steps": [
                    {
                        "step_id": "producer:g2",
                        "tool_name": "query_order",
                        "input_bindings": [],
                    },
                    {
                        "step_id": "consumer:g2",
                        "tool_name": "logistics",
                        "input_bindings": [
                            {
                                "binding_kind": "step_output",
                                "producer_tool": "query_order",
                                "producer_step_id": "producer:g2",
                                "type_name": "VerifiedOrder",
                            }
                        ],
                    },
                ],
            },
        },
    ]
    coverage = build_goal_capability_coverage(
        goals=[],
        goal_plans=goal_plans,
        capability_registry=_registry(),
    )

    assert coverage["shared_prerequisite_bindings"] == [
        {
            "type_name": "VerifiedOrder",
            "producer_tool": "query_order",
            "consumers": [
                {
                    "goal_id": "g1",
                    "consumer_tool": "details",
                    "consumer_step_id": "consumer:g1",
                    "producer_step_id": "producer:g1",
                },
                {
                    "goal_id": "g2",
                    "consumer_tool": "logistics",
                    "consumer_step_id": "consumer:g2",
                    "producer_step_id": "producer:g2",
                },
            ],
            "reuse_owner": "goal_output_refs",
            "reuse_rule": (
                "one_active_scope_bound_verified_output_may_feed_"
                "dependency_ready_goals"
            ),
            "requires_runtime_target_compatibility": True,
        }
    ]


def test_shared_multi_goal_capability_requires_v2_primary_completion_proof() -> None:
    goals = [
        {
            "goal_id": "g1",
            "required": True,
            "requested_effect": {"domain": "order", "operation": "query_details", "object_type": "order"},
            "target_candidate": {"order_id": "10002"},
        },
        {
            "goal_id": "g2",
            "required": True,
            "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
            "target_candidate": {"order_id": "10002"},
        },
    ]
    coverage = build_goal_capability_coverage(
        goals=goals,
        goal_plans=[],
        capability_registry=_registry(contract_v2=False),
    )

    assert coverage["coverage_status"] == "COMPLETE"
    assert coverage["shared_capability_bindings"] == []
    rejection = coverage["shared_capability_rejections"][0]
    assert rejection["tool_name"] == "get_order_overview"
    assert rejection["goal_ids"] == ["g1", "g2"]
    assert rejection["reasons"] == ["capability_contract_v2_required"]
    assert rejection["target_compatibility"]["status"] == "SAME"


def test_shared_write_capability_is_not_a_single_dispatch_candidate() -> None:
    goals = [
        {
            "goal_id": "g1",
            "required": True,
            "requested_effect": {"domain": "order", "operation": "query_details", "object_type": "order"},
            "target_candidate": {"order_id": "10002"},
        },
        {
            "goal_id": "g2",
            "required": True,
            "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
            "target_candidate": {"order_id": "10002"},
        },
    ]
    coverage = build_goal_capability_coverage(
        goals=goals,
        goal_plans=[],
        capability_registry=_registry(writes=True),
    )

    assert coverage["shared_capability_bindings"] == []
    assert "shared_write_requires_explicit_atomic_business_strategy" in (
        coverage["shared_capability_rejections"][0]["reasons"]
    )


def test_global_coverage_rejects_shared_dispatch_for_different_explicit_targets() -> None:
    goals = [
        {
            "goal_id": "g1",
            "required": True,
            "requested_effect": {"domain": "order", "operation": "query_details", "object_type": "order"},
            "target_candidate": {"order_id": "10001"},
        },
        {
            "goal_id": "g2",
            "required": True,
            "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
            "target_candidate": {"order_id": "10002"},
        },
    ]

    coverage = build_goal_capability_coverage(
        goals=goals,
        goal_plans=[],
        capability_registry=_registry(),
    )

    assert coverage["shared_capability_bindings"] == []
    rejection = coverage["shared_capability_rejections"][0]
    assert rejection["tool_name"] == "get_order_overview"
    assert rejection["goal_ids"] == ["g1", "g2"]
    assert "shared_target_mismatch" in rejection["reasons"]


def test_global_coverage_fails_closed_when_shared_target_identity_is_unknown() -> None:
    goals = [
        {
            "goal_id": "g1",
            "required": True,
            "requested_effect": {"domain": "order", "operation": "query_details", "object_type": "order"},
        },
        {
            "goal_id": "g2",
            "required": True,
            "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        },
    ]
    coverage = build_goal_capability_coverage(
        goals=goals, goal_plans=[], capability_registry=_registry()
    )

    assert coverage["shared_capability_bindings"] == []
    assert "shared_target_unproven" in coverage["shared_capability_rejections"][0]["reasons"]
