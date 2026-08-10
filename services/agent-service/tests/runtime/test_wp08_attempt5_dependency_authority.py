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
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        return_value=_response({
            "verdict": "exact",
            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
            "missing_spans": [],
            "dependency_edges": edges,
            "reason_code": "all_requested_outcomes_and_dependency_preserved",
        }),
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"][0]["basis_span"] == "它"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"


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


def test_exact_contradictory_graph_self_reaudits_before_touching_candidate() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", ["g1"])]
    edge = {"dependent_goal_id": "g2", "requires_result_of_goal_id": "g1", "basis_kind": "result_reference", "basis_span": "它"}
    calls = [
        _response({"verdict": "exact", "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"], "missing_spans": [], "dependency_edges": [], "reason_code": "contradictory_exact"}),
        _response({"verdict": "exact", "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"], "missing_spans": [], "dependency_edges": [edge], "reason_code": "dependency_reaudit_exact"}),
    ]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["verifier_repair_kind"] == "dependency_proof_reaudit"


def test_malformed_alignment_basis_fails_closed_after_bounded_reaudit() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", ["g1"])]
    bad = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_edges": [{"dependent_goal_id": "g2", "requires_result_of_goal_id": "g1", "basis_kind": "result_reference", "basis_span": "键盘订单"}],
        "reason_code": "bad_basis",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[bad, bad]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_alignment_dependency_basis_not_in_dependent_goal:0"


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
