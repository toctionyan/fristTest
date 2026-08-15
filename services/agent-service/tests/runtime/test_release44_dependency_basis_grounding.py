from __future__ import annotations

from agent_core.lifecycle.goal_planning import (
    _model_alignment_dependency_proof,
    _model_alignment_pairwise_dependency_proof,
)


def _goal(
    goal_id: str,
    span: str,
    depends_on: list[str],
    *,
    output_span: str | None = None,
) -> dict:
    requested_effect = {
        "domain": "open",
        "operation": "open",
        "object_type": "order",
        "raw_description": span,
    }
    if output_span is not None:
        requested_effect["requested_outputs"] = [{
            "output_id": "open",
            "evidence_span": output_span,
            "open_description": "domain-neutral requested outcome",
        }]
    return {
        "goal_id": goal_id,
        "evidence_span": span,
        "depends_on": depends_on,
        "requested_effect": requested_effect,
    }


def test_release44_action_phrase_cannot_self_certify_as_result_dependency_basis() -> None:
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [
        _goal("g1", "查一下鼠标订单", []),
        _goal("g2", "帮我申请退款", ["g1"], output_span="申请退款"),
    ]
    edge = [{
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "帮我申请退款",
    }]
    details, error = _model_alignment_dependency_proof(
        user_text=text,
        goals=goals,
        values=edge,
    )
    assert details["dependency_proof_complete"] is False
    assert error == "goal_alignment_dependency_basis_is_requested_output:0"


def test_release44_blind_action_phrase_cannot_self_certify_as_result_dependency_basis() -> None:
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [
        _goal("g1", "查一下鼠标订单", []),
        _goal("g2", "帮我申请退款", ["g1"], output_span="申请退款"),
    ]
    decisions = [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "b_depends_on_a",
        "basis_kind": "result_value_input",
        "basis_span": "帮我申请退款",
    }]
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text=text,
        goals=goals,
        values=decisions,
    )
    assert details["dependency_proof_complete"] is False
    assert error == "goal_alignment_dependency_basis_is_requested_output:0"


def test_relation_only_reference_stays_valid_when_disjoint_from_requested_output() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [
        _goal("g1", "查一下键盘订单", []),
        _goal("g2", "再看看它能不能退款", ["g1"], output_span="能不能退款"),
    ]
    edge = [{
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }]
    details, error = _model_alignment_dependency_proof(
        user_text=text,
        goals=goals,
        values=edge,
    )
    assert error is None
    assert details["dependency_proof_complete"] is True
    assert details["dependency_graph_match"] is True

    blind, blind_error = _model_alignment_pairwise_dependency_proof(
        user_text=text,
        goals=goals,
        values=[{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }],
    )
    assert blind_error is None
    assert blind["dependency_proof_complete"] is True
    assert blind["dependency_graph_match"] is True


def test_broad_true_relation_basis_must_be_narrowed_instead_of_wrapping_output() -> None:
    text = "Inspect record A, then use that result to open a service request"
    goals = [
        _goal("g1", "Inspect record A", []),
        _goal(
            "g2",
            "use that result to open a service request",
            ["g1"],
            output_span="open a service request",
        ),
    ]
    details, error = _model_alignment_dependency_proof(
        user_text=text,
        goals=goals,
        values=[{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "use that result to open a service request",
        }],
    )
    assert details["dependency_proof_complete"] is False
    assert error == "goal_alignment_dependency_basis_is_requested_output:0"
