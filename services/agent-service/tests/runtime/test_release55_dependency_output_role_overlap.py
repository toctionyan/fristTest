from __future__ import annotations

import inspect

from agent_core.lifecycle.goal_planning import (
    ModelGoalAlignmentVerifier,
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
            "evidence_span": "它能不能退款",
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


def _pair(*, relation: str = "b_depends_on_a", basis_span: str = "它") -> list[dict[str, str]]:
    return [
        {
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": relation,
            "basis_kind": "result_reference",
            "basis_span": basis_span,
        }
    ]


def test_strict_nested_relation_basis_reaches_dependency_graph_adjudication() -> None:
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text="查一下键盘订单，再看看它能不能退款",
        goals=_goals(),
        values=_pair(),
    )

    assert error == "goal_alignment_dependency_graph_mismatch"
    assert details["dependency_edges"] == [
        {
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }
    ]
    assert details["dependency_pair_decisions"][0]["relation"] == "b_depends_on_a"
    # Pair coverage is structurally complete, but it disagrees with the declared
    # graph and therefore is not accepted dependency authority.
    assert details["dependency_proof_complete"] is True
    assert details["dependency_graph_match"] is False


def test_equal_requested_output_evidence_cannot_be_dependency_basis() -> None:
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text="查一下键盘订单，再看看它能不能退款",
        goals=_goals(),
        values=_pair(basis_span="它能不能退款"),
    )

    assert error == "goal_alignment_dependency_basis_is_requested_output:0"
    assert details["dependency_edges"] == []
    assert details["dependency_proof_complete"] is False


def test_dependency_basis_wrapping_requested_output_evidence_stays_rejected() -> None:
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text="查一下键盘订单，再看看它能不能退款",
        goals=_goals(output_span="退款"),
        values=_pair(basis_span="它能不能退款"),
    )

    assert error == "goal_alignment_dependency_basis_is_requested_output:0"
    assert details["dependency_edges"] == []
    assert details["dependency_proof_complete"] is False


def test_nonliteral_dependency_basis_remains_fail_closed() -> None:
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text="查一下键盘订单，再看看它能不能退款",
        goals=_goals(),
        values=_pair(basis_span="前一个查询结果"),
    )

    assert error == "goal_alignment_dependency_basis_not_in_dependent_goal:0"
    assert details["dependency_edges"] == []
    assert details["dependency_proof_complete"] is False


def test_nested_relation_basis_is_only_provisional_not_authority() -> None:
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text="查一下键盘订单，再看看它能不能退款",
        goals=_goals(),
        values=_pair(),
    )

    # Structural admissibility must not turn an observed positive pair into
    # accepted dependency authority. With an empty declared graph this remains
    # a mismatch that the existing counterfactual/adversarial closure must judge.
    assert error == "goal_alignment_dependency_graph_mismatch"
    assert details["dependency_graph_match"] is False
    assert details["dependency_proof_complete"] is True


def test_candidate_blind_dependency_prompt_matches_nested_basis_structural_contract() -> None:
    """Semantic verifier instructions must match the structural basis boundary."""

    source = inspect.getsource(ModelGoalAlignmentVerifier.verify)

    assert "must be disjoint from the dependent Goal requested_outputs evidence spans" not in source
    assert "if no disjoint relation-only basis exists" not in source
    assert (
        "a strictly smaller relation-only literal basis nested inside a broader requested-output evidence span is admissible"
        in source
    )
    assert "must not equal a requested-output evidence span" in source
    assert "must not wrap a requested-output evidence span with action/control wording" in source
