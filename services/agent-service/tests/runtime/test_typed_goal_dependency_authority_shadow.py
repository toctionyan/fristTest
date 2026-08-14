from __future__ import annotations

from copy import deepcopy

from agent_core.goal_graph.contracts import (
    make_verified_artifact_ref,
    make_verified_dataflow_edge,
    with_verified_dataflow_edge,
)
from agent_core.lifecycle.goal_capability_coverage import build_goal_capability_coverage
from agent_core.lifecycle.pretool_execution_policy import (
    build_pretool_execution_policy,
    execution_policy_prompt_projection,
)
from agent_core.lifecycle.pretool_planner import build_pretool_shadow_plan
from tests.runtime.test_pretool_execution_policy import _contract, _goal, _registry


_SCOPE = {
    "current_tenant_id": "tenant-1",
    "current_user_id": "u001",
    "current_thread_id": "web-u001-stage2c",
}


def _state(contract: dict, **extra) -> dict:
    return {
        "frozen_semantic_contract": contract,
        **_SCOPE,
        **extra,
    }


def test_dependency_shadow_marks_matching_closed_graph_as_cutover_candidate_only() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )

    shadow = policy["typed_dependency_authority_shadow"]
    assert shadow["status"] == "MATCHED"
    assert shadow["typed_dataflow_status"] == "GOAL_GRAPH_DATAFLOW_CLOSED"
    assert shadow["cutover_eligible"] is True
    assert shadow["cutover_performed"] is False
    assert shadow["current_dependency_authority"] == "legacy_declared_goal_dependencies"
    assert shadow["candidate_dependency_authority"] == "verified_dataflow_edges_only"
    assert shadow["changes_current_dependency_blocking"] is False
    assert shadow["changes_allowed_capability_tools"] is False
    assert shadow["blocks_execution"] is False
    assert shadow["creates_permit"] is False
    assert policy["allowed_capability_tools"] == ["get_order_details"]


def test_unverified_legacy_dependency_stays_blocked_by_current_policy_but_shadow_is_not_ready() -> None:
    contract = _contract([
        _goal("refund", domain="refund", operation="create"),
        _goal("invoice", domain="invoice", operation="create", depends_on=("refund",)),
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract, goal_records=[]),
        capability_registry=_registry(),
    )
    by_goal = {row["goal_id"]: row for row in policy["goal_policies"]}

    assert by_goal["invoice"]["status"] == "BLOCKED_BY_GOAL_DEPENDENCY"
    assert "prepare_invoice" not in policy["allowed_capability_tools"]
    shadow = policy["typed_dependency_authority_shadow"]
    assert shadow["status"] == "NOT_READY_DATAFLOW_OPEN"
    assert shadow["typed_dataflow_status"] == "GOAL_GRAPH_DATAFLOW_OPEN"
    assert shadow["cutover_eligible"] is False
    invoice = next(row for row in shadow["comparisons"] if row["goal_id"] == "invoice")
    assert invoice["legacy_dependency_goal_ids"] == ["refund"]
    assert invoice["typed_derived_dependency_goal_ids"] == []
    assert invoice["current_legacy_would_block"] is True
    assert invoice["typed_would_block"] is None
    assert "LEGACY_DEPENDENCY_NOT_VERIFIED_BY_DATAFLOW" in invoice["codes"]
    assert shadow["changes_current_dependency_blocking"] is False


def test_verified_dataflow_dependency_missing_from_legacy_is_visible_without_changing_frontier() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details"),
        {
            **_goal("invoice", domain="invoice", operation="create"),
            "target_candidate": {"order_id": "10002"},
        },
    ])
    registry = _registry()
    state = _state(contract, goal_records=[])
    plan = build_pretool_shadow_plan(state=state, capability_registry=registry)
    graph = deepcopy(plan["typed_goal_graph"])
    by_goal = {row["goal_id"]: row for row in graph["goals"]}
    producer = by_goal["details"]
    consumer = by_goal["invoice"]
    source = graph["source_semantic_contract"]
    artifact = make_verified_artifact_ref(
        artifact_ref="order:10002",
        type_name="ResolvedOrderBinding",
        resource_type="order",
        cardinality="exactly_one",
        producer_goal_id="details",
        scope=graph["scope"],
        semantic_contract_id=source["semantic_contract_id"],
        semantic_digest=source["semantic_digest"],
        source_ref_id="test:verified-order-binding",
        proof_digest="a" * 64,
        authority="authoritative",
    )
    edge = make_verified_dataflow_edge(
        graph=graph,
        producer_goal_id="details",
        producer_port_id=producer["output_ports"][0]["port_id"],
        consumer_goal_id="invoice",
        consumer_port_id=consumer["input_ports"][0]["port_id"],
        artifact_ref=artifact,
        verification_proof_digest="b" * 64,
    )
    graph = with_verified_dataflow_edge(graph, edge)
    coverage = build_goal_capability_coverage(
        goals=contract["goals"],
        goal_plans=plan["goal_plans"],
        capability_registry=registry,
        typed_goal_graph=graph,
        frozen_contract=contract,
    )
    plan["typed_goal_graph"] = graph
    plan["global_goal_capability_coverage"] = coverage

    baseline = build_pretool_execution_policy(
        state=state,
        capability_registry=registry,
    )
    policy = build_pretool_execution_policy(
        state=state,
        capability_registry=registry,
        shadow_plan=plan,
    )

    assert policy["allowed_capability_tools"] == baseline["allowed_capability_tools"]
    by_policy_goal = {row["goal_id"]: row for row in policy["goal_policies"]}
    assert by_policy_goal["invoice"]["status"] != "BLOCKED_BY_GOAL_DEPENDENCY"
    shadow = policy["typed_dependency_authority_shadow"]
    assert shadow["status"] == "DIVERGED"
    assert shadow["typed_dataflow_status"] == "GOAL_GRAPH_DATAFLOW_CLOSED"
    assert shadow["cutover_eligible"] is False
    invoice = next(row for row in shadow["comparisons"] if row["goal_id"] == "invoice")
    assert invoice["legacy_dependency_goal_ids"] == []
    assert invoice["typed_derived_dependency_goal_ids"] == ["details"]
    assert invoice["current_legacy_would_block"] is False
    assert invoice["typed_would_block"] is True
    assert "VERIFIED_DATAFLOW_DEPENDENCY_MISSING_FROM_LEGACY" in invoice["codes"]
    assert shadow["changes_current_dependency_blocking"] is False
    assert shadow["changes_allowed_capability_tools"] is False


def test_dependency_shadow_is_not_exposed_to_model_prompt_projection() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )
    projection = execution_policy_prompt_projection(policy)

    assert "typed_dependency_authority_shadow" in policy
    assert "typed_dependency_authority_shadow" not in projection
    assert "typed_graph" not in projection
