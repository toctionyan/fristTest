from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from agent_core.lifecycle.goal_granularity import (
    GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION,
    ModelGoalGranularityVerifier,
    verify_goal_granularity,
)
from agent_core.lifecycle.goal_planning import (
    ModelGoalAlignmentVerifier,
    _model_alignment_dependency_proof,
)


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {"goal_id": goal_id, "evidence_span": span, "depends_on": depends_on}


def _response(payload: dict) -> tuple[SimpleNamespace, dict]:
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def test_true_result_reference_is_owned_by_grounded_alignment_dependency_proof() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", ["g1"])]
    edges = [{"dependent_goal_id": "g2", "requires_result_of_goal_id": "g1", "basis_kind": "result_reference", "basis_span": "它"}]
    details, error = _model_alignment_dependency_proof(user_text=text, goals=goals, values=edges)
    assert error is None
    assert details["dependency_graph_match"] is True
    candidate = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_edges": edges,
        "reason_code": "all_requested_outcomes_and_dependency_preserved",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }],
        "reason_code": "all_requested_outcomes_and_dependency_preserved",
    })
    adversarial_confirmation = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }],
        "reason_code": "adversarial_true_result_reference",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[candidate, blind, adversarial_confirmation]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"][0]["basis_span"] == "它"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_positive_edge_adjudication"


def test_shared_scope_ellipsis_remains_independent() -> None:
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [_goal("g1", "查一下鼠标订单", []), _goal("g2", "帮我申请退款", [])]
    details, error = _model_alignment_dependency_proof(user_text=text, goals=goals, values=[])
    assert error is None
    assert details["dependency_graph_match"] is True


def test_false_declared_dependency_is_rejected_by_independent_alignment_graph() -> None:
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [_goal("g1", "查一下鼠标订单", []), _goal("g2", "帮我申请退款", ["g1"])]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        return_value=_response({
            "verdict": "incomplete",
            "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "declared_dependency_not_expressed",
        }),
    ):
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.details["dependency_graph_match"] is False


def test_exact_contradictory_graph_self_reaudits_candidate_blind() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", ["g1"])]
    calls = [
        _response({
            "verdict": "exact",
            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "contradictory_exact",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
            "missing_spans": [],
            "dependency_decisions": [{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "b_depends_on_a",
                "basis_kind": "result_reference",
                "basis_span": "它",
            }],
            "reason_code": "blind_dependency_reaudit_exact",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
            "missing_spans": [],
            "dependency_decisions": [{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "b_depends_on_a",
                "basis_kind": "result_reference",
                "basis_span": "它",
            }],
            "reason_code": "adversarial_dependency_confirmation",
        }),
    ]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_positive_edge_adjudication"


def test_malformed_alignment_basis_fails_closed_after_blind_reaudit() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", ["g1"])]
    bad = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "键盘订单",
        }],
        "reason_code": "bad_basis",
    })
    blind_bad = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "键盘订单",
        }],
        "reason_code": "bad_blind_basis",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[bad, blind_bad, blind_bad]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 3
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_alignment_dependency_basis_not_in_dependent_goal:0"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_format_repair"
    third_payload = str(invoke.call_args_list[2].kwargs["payload"])
    assert "same-turn target identity inherited by zero-anaphora" in third_payload
    assert "never emit that identity phrase as a target-scope-constraint missing span" in third_payload


def test_unproposed_refund_dependency_requires_candidate_blind_confirmation() -> None:
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [_goal("g1", "查一下鼠标订单", []), _goal("g2", "帮我申请退款", [])]
    false_positive = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "帮我申请退款",
        }],
        "reason_code": "refund_needs_query_result",
    })
    blind_independent = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "independent",
        }],
        "reason_code": "same_turn_scope_is_not_result_dependency",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[false_positive, blind_independent, blind_independent]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_authority_state"] == "authoritative"
    assert verdict.details["dependency_challenge_required"] is False
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_authority_closure"
    second_payload = str(invoke.call_args_list[1].kwargs["payload"])
    assert '"depends_on"' not in second_payload
    assert "same-turn zero-anaphora" in second_payload
    assert "Never report such inherited identity text as a target-scope-constraint missing span" in second_payload



def test_dependency_format_repair_reuses_first_pass_grounded_outcome_evidence() -> None:
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [_goal("g1", "查一下鼠标订单", []), _goal("g2", "帮我申请退款", [])]
    false_positive = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "帮我申请退款",
        }],
        "reason_code": "refund_needs_query_result",
    })
    malformed_blind = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "鼠标订单",
        }],
        "reason_code": "malformed_blind_basis",
    })
    repaired_blind_without_duplicate_evidence = _response({
        "verdict": "exact",
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "independent",
        }],
        "reason_code": "repaired_pairwise_dependency_proof",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[
            false_positive,
            malformed_blind,
            repaired_blind_without_duplicate_evidence,
            repaired_blind_without_duplicate_evidence,
        ],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 4
    assert verdict.exact
    assert verdict.evidence_spans == ("查一下鼠标订单", "帮我申请退款")
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_authority_state"] == "authoritative"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_authority_closure"


def test_unknown_and_self_dependency_edges_fail_closed() -> None:
    text = "查订单并继续处理"
    goals = [_goal("g1", "查订单", [])]
    _, unknown = _model_alignment_dependency_proof(
        user_text=text,
        goals=goals,
        values=[{"dependent_goal_id": "g1", "requires_result_of_goal_id": "g9", "basis_kind": "result_reference", "basis_span": "查订单"}],
    )
    assert unknown == "goal_alignment_dependency_prerequisite_goal_unknown:0"
    _, self_edge = _model_alignment_dependency_proof(
        user_text=text,
        goals=goals,
        values=[{"dependent_goal_id": "g1", "requires_result_of_goal_id": "g1", "basis_kind": "result_reference", "basis_span": "查订单"}],
    )
    assert self_edge == "goal_alignment_dependency_self_edge:0"


def test_blind_granularity_is_outcome_only_and_one_call_on_structural_match() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", ["g1"])]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        return_value=_response({
            "verdict": "exact",
            "outcome_spans": ["查一下键盘订单", "再看看它能不能退款"],
            "reason_code": "two_observable_outcomes",
            "dependency_edges": [],
        }),
    ) as invoke:
        verdict = ModelGoalGranularityVerifier().verify(user_text=text, goals=goals)
    assert invoke.call_count == 1
    assert verdict.exact
    assert verdict.details["authority_scope"] == "outcome_inventory_only"
    authority = verdict.details["inventory_authority"]
    assert authority["version"] == GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION
    assert "dependency_edges" not in authority


def test_frozen_outcome_authority_never_reinterprets_dependency_graph() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    with_dependency = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", ["g1"])]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        return_value=_response({"verdict": "exact", "outcome_spans": ["查一下键盘订单", "再看看它能不能退款"], "reason_code": "two_observable_outcomes"}),
    ):
        first = ModelGoalGranularityVerifier().verify(user_text=text, goals=with_dependency)
    authority = first.details["inventory_authority"]
    without_dependency = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", [])]
    with patch("agent_core.config.get_model", side_effect=AssertionError("frozen authority must not call model")):
        reused = verify_goal_granularity(
            state={"current_user_input": text, "current_turn_plan": {"goal_granularity_inventory_authority": authority}},
            goals=without_dependency,
        )
    assert reused.exact
    assert reused.details["inventory_authority_reused"] is True
    assert reused.details["dependency_authority"] == "independent_goal_alignment"



def test_release43_missing_dependency_without_redundant_outcome_evidence_is_repairable() -> None:
    """Release #43: a machine-grounded graph mismatch must reach redeclaration."""
    from agent_core.lifecycle.goal_planning import _alignment_repair_feedback

    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", [])]
    edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }
    first = _response({
        "verdict": "incomplete",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_edges": [edge],
        "reason_code": "missing_true_result_dependency",
    })
    blind = _response({
        "verdict": "exact",
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }],
        "reason_code": "candidate_blind_true_result_reference",
    })

    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.evidence_spans == ()
    assert verdict.missing_spans == ()
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["dependency_edges"] == [edge]
    assert verdict.details["dependency_mismatch_grounding"] == "machine_dependency_proof"
    assert verdict.details["dependency_authority_state"] == "authoritative"
    assert verdict.details["dependency_challenge_required"] is False
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_authority_closure"

    feedback = _alignment_repair_feedback(verdict)["independent_verifier_feedback"]
    assert feedback["required_action"] == "redeclaration_preserving_grounded_dependency_graph"
    assert feedback["dependency_edges"] == [edge]
    assert feedback["candidate_declared_dependency_edges"] == []


def test_release43_machine_dependency_proof_never_certifies_exact_without_outcome_evidence() -> None:
    """The repair changes rejection classification only; exact still needs literal outcome evidence."""
    from agent_core.lifecycle.goal_planning import GoalAlignmentVerdict, _as_alignment_verdict

    text = "查一下键盘订单，再看看它能不能退款"
    edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }
    normalized = _as_alignment_verdict(
        GoalAlignmentVerdict(
            "exact",
            (),
            (),
            "all_requested_outcomes_and_dependency_preserved",
            "model",
            True,
            {
                "dependency_authority": "independent_goal_alignment",
                "dependency_proof_complete": True,
                "dependency_graph_match": True,
                "declared_dependency_edges": [{
                    "dependent_goal_id": "g2",
                    "requires_result_of_goal_id": "g1",
                }],
                "dependency_edges": [edge],
            },
        ),
        user_text=text,
        source="model",
        independent=True,
    )
    assert normalized.verdict == "indeterminate"
    assert normalized.reason_code == "goal_alignment_evidence_not_in_current_user_text"
