from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from agent_core.goal_graph.dependency_basis_contract import (
    FINAL_DEPENDENCY_AUTHORITY,
    canonical_contract,
    dependency_basis_conflicts_with_requested_outputs,
    mutation_detection_matrix,
    projection_manifest,
    render_candidate_blind_dependency_rule,
    render_dependency_format_repair_rule,
    verify_projection_manifest,
)
from agent_core.lifecycle.goal_planning import (
    ModelGoalAlignmentVerifier,
    _dependency_basis_overlaps_requested_output,
    _model_alignment_pairwise_dependency_proof,
)


def _goals(*, output_span: str = "它能不能退款") -> list[dict[str, object]]:
    return [
        {
            "goal_id": "g1",
            "evidence_span": "查一下键盘订单",
            "requested_effect": {
                "domain": "order",
                "operation": "list",
                "object_type": "order",
                "requested_outputs": [
                    {
                        "output_id": "order.collection",
                        "evidence_span": "查一下键盘订单",
                    }
                ],
            },
            "depends_on": [],
        },
        {
            "goal_id": "g2",
            "evidence_span": output_span,
            "requested_effect": {
                "domain": "refund",
                "operation": "assess_eligibility",
                "object_type": "order",
                "requested_outputs": [
                    {
                        "output_id": "refund.eligibility",
                        "evidence_span": output_span,
                    }
                ],
            },
            "depends_on": [],
        },
    ]


def _pair(*, basis_span: str) -> list[dict[str, str]]:
    return [
        {
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": basis_span,
        }
    ]


@pytest.mark.parametrize(
    ("dependent_span", "basis_span"),
    [
        ("它能不能退款", "它"),
        ("它是否可以退款", "它"),
        ("看看它可以退款吗", "它"),
    ],
)
def test_nested_relation_only_basis_variants_reach_adjudication(
    dependent_span: str,
    basis_span: str,
) -> None:
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text=f"查一下键盘订单，再{dependent_span}",
        goals=_goals(output_span=dependent_span),
        values=_pair(basis_span=basis_span),
    )

    assert error == "goal_alignment_dependency_graph_mismatch"
    assert details["dependency_proof_complete"] is True
    assert details["dependency_graph_match"] is False
    assert details["dependency_edges"] == [
        {
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": basis_span,
        }
    ]


def test_equal_requested_output_is_rejected_by_canonical_projection() -> None:
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text="查一下键盘订单，再看看它能不能退款",
        goals=_goals(),
        values=_pair(basis_span="它能不能退款"),
    )
    assert error == "goal_alignment_dependency_basis_is_requested_output:0"
    assert details["dependency_edges"] == []


def test_basis_wrapping_requested_output_is_rejected() -> None:
    goals = _goals(output_span="退款")
    goals[1]["evidence_span"] = "它能不能退款"
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text="查一下键盘订单，再看看它能不能退款",
        goals=goals,
        values=_pair(basis_span="它能不能退款"),
    )
    assert error == "goal_alignment_dependency_basis_is_requested_output:0"
    assert details["dependency_edges"] == []


def test_independent_multi_intent_does_not_gain_dependency_edge() -> None:
    goals = [
        {
            "goal_id": "g1",
            "evidence_span": "查一下键盘订单",
            "requested_effect": {
                "requested_outputs": [
                    {"output_id": "order.collection", "evidence_span": "查一下键盘订单"}
                ]
            },
            "depends_on": [],
        },
        {
            "goal_id": "g2",
            "evidence_span": "再查一下优惠券",
            "requested_effect": {
                "requested_outputs": [
                    {"output_id": "coupon.collection", "evidence_span": "再查一下优惠券"}
                ]
            },
            "depends_on": [],
        },
    ]
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text="查一下键盘订单，再查一下优惠券",
        goals=goals,
        values=[
            {
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "independent",
            }
        ],
    )
    assert error is None
    assert details["dependency_proof_complete"] is True
    assert details["dependency_graph_match"] is True
    assert details["dependency_edges"] == []


def test_contract_has_no_final_edge_authority() -> None:
    contract = canonical_contract()
    assert contract["authority"]["authority_effect"] is False
    assert contract["authority"]["final_dependency_authority"] == FINAL_DEPENDENCY_AUTHORITY
    assert FINAL_DEPENDENCY_AUTHORITY == "deterministic_dependency_proof_reducer"


def test_goal_planning_uses_generated_semantic_and_structural_projections() -> None:
    verifier_source = inspect.getsource(ModelGoalAlignmentVerifier.verify)
    overlap_source = inspect.getsource(_dependency_basis_overlaps_requested_output)

    assert "render_candidate_blind_dependency_rule()" in verifier_source
    assert "render_dependency_format_repair_rule()" in verifier_source
    assert "must be disjoint from the dependent Goal requested_outputs evidence spans" not in verifier_source
    assert "if no disjoint relation-only basis exists" not in verifier_source

    assert "dependency_basis_conflicts_with_requested_outputs" in overlap_source
    assert "basis == output_span" not in overlap_source
    assert "output_span in basis" not in overlap_source


def test_projection_manifest_is_exact_and_provenanced() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    path = repo_root / "contracts" / "generated" / "dependency-basis-projections.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert verify_projection_manifest(payload)
    assert payload == projection_manifest()
    assert payload["authority_effect"] is False
    assert payload["final_dependency_authority"] == FINAL_DEPENDENCY_AUTHORITY
    assert payload["rules"] == canonical_contract()["rules"]
    assert payload["basis"] == canonical_contract()["basis"]


def test_nested_rule_is_executable_source_for_behavior_and_prompt_projection() -> None:
    canonical = canonical_contract()
    mutated = canonical_contract()
    mutated["rules"]["strict_nested_requested_output"] = "forbidden"

    assert dependency_basis_conflicts_with_requested_outputs(
        "它", ["它能不能退款"], payload=canonical
    ) is False
    assert dependency_basis_conflicts_with_requested_outputs(
        "它", ["它能不能退款"], payload=mutated
    ) is True
    assert render_candidate_blind_dependency_rule(mutated) != render_candidate_blind_dependency_rule(
        canonical
    )
    assert projection_manifest(mutated)["rules"]["strict_nested_requested_output"] == "forbidden"


def test_equality_and_wrapper_rules_drive_structural_behavior() -> None:
    canonical = canonical_contract()
    equality_allowed = canonical_contract()
    equality_allowed["rules"]["requested_output_equality"] = "allowed"
    wrapper_allowed = canonical_contract()
    wrapper_allowed["rules"]["requested_output_wrapper"] = "allowed"

    assert dependency_basis_conflicts_with_requested_outputs(
        "它能不能退款", ["它能不能退款"], payload=canonical
    ) is True
    assert dependency_basis_conflicts_with_requested_outputs(
        "它能不能退款", ["它能不能退款"], payload=equality_allowed
    ) is False
    assert dependency_basis_conflicts_with_requested_outputs(
        "看看它能不能退款", ["退款"], payload=canonical
    ) is True
    assert dependency_basis_conflicts_with_requested_outputs(
        "看看它能不能退款", ["退款"], payload=wrapper_allowed
    ) is False


def test_independence_fallback_rule_drives_both_model_projections() -> None:
    canonical = canonical_contract()
    mutated = canonical_contract()
    mutated["rules"]["no_valid_relation_only_basis"] = "dependent"

    assert render_candidate_blind_dependency_rule(mutated) != render_candidate_blind_dependency_rule(
        canonical
    )
    assert render_dependency_format_repair_rule(mutated) != render_dependency_format_repair_rule(
        canonical
    )
    assert "relation=dependent" in render_candidate_blind_dependency_rule(mutated)
    assert "relation=dependent" in render_dependency_format_repair_rule(mutated)


def test_mutation_gate_detects_each_representative_drift() -> None:
    results = mutation_detection_matrix()
    assert results
    assert all(results.values()), results
