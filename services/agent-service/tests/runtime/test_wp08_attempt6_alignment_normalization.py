from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from agent_core.lifecycle.goal_planning import (
    GoalAlignmentVerdict,
    _as_alignment_verdict,
    validate_goal_declaration,
    verify_goal_alignment,
)


def _response(payload: dict) -> tuple[SimpleNamespace, dict]:
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {"domain": "commerce", "operation": "inspect", "object_type": "order"},
        "expected_result_cardinality": "collection",
        "required": True,
        "depends_on": depends_on,
    }


def _mismatch_details() -> dict:
    return {
        "dependency_authority": "independent_goal_alignment",
        "dependency_proof_complete": True,
        "dependency_graph_match": False,
        "declared_dependency_edges": [{"dependent_goal_id": "g2", "requires_result_of_goal_id": "g1"}],
        "dependency_edges": [],
    }


def test_internal_dependency_mismatch_incomplete_survives_outer_normalization() -> None:
    text = "查一下我的订单，再查下物流到哪了"
    raw = GoalAlignmentVerdict(
        "incomplete", ("查一下我的订单", "再查下物流到哪了"), (),
        "goal_alignment_dependency_graph_mismatch", "model", True, _mismatch_details(),
    )
    verdict = _as_alignment_verdict(raw, user_text=text, source="model", independent=True)
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ()
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["incomplete_grounding"] == "dependency_graph_mismatch"


def test_generic_incomplete_without_literal_missing_span_still_fails_closed() -> None:
    text = "查一下我的订单，再查下物流到哪了"
    raw = GoalAlignmentVerdict(
        "incomplete", ("查一下我的订单", "再查下物流到哪了"), (),
        "some_coverage_claim", "model", True, {},
    )
    verdict = _as_alignment_verdict(raw, user_text=text, source="model", independent=True)
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_alignment_missing_span_not_grounded"


def test_untrusted_dict_cannot_forge_dependency_mismatch_incomplete_exception() -> None:
    text = "查一下我的订单，再查下物流到哪了"
    verdict = _as_alignment_verdict(
        {
            "verdict": "incomplete",
            "evidence_spans": ["查一下我的订单", "再查下物流到哪了"],
            "missing_spans": [],
            "reason_code": "goal_alignment_dependency_graph_mismatch",
            "source": "model",
            "independent": True,
            "details": _mismatch_details(),
        },
        user_text=text,
        source="injected",
        independent=True,
    )
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_alignment_missing_span_not_grounded"


def test_literal_missing_outcome_remains_valid_coverage_incomplete() -> None:
    text = "查订单并查物流"
    raw = GoalAlignmentVerdict("incomplete", ("查订单",), ("查物流",), "missing_logistics", "model", True, {})
    verdict = _as_alignment_verdict(raw, user_text=text, source="model", independent=True)
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("查物流",)


def _reaudit_calls() -> list[tuple[SimpleNamespace, dict]]:
    return [
        _response({
            "verdict": "exact",
            "evidence_spans": ["查一下我的订单", "再查下物流到哪了"],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "coverage_exact_but_false_dependency_declared",
        }),
        _response({
            "verdict": "incomplete",
            "evidence_spans": ["查一下我的订单", "再查下物流到哪了"],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "declared_dependency_not_expressed",
        }),
    ]


def test_attempt6_two_call_dependency_reaudit_returns_repairable_incomplete_not_unverified() -> None:
    text = "查一下我的订单，再查下物流到哪了"
    goals = [_goal("g1", "查一下我的订单", []), _goal("g2", "再查下物流到哪了", ["g1"])]
    with patch("agent_core.lifecycle.goal_planning._goal_alignment_mode", return_value="model"), patch(
        "agent_core.config.get_model", return_value=object()
    ), patch("agent_core.model_calls.invoke_model", side_effect=_reaudit_calls()) as invoke:
        verdict = verify_goal_alignment(state={"current_user_input": text}, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["verifier_repair_kind"] == "dependency_proof_reaudit"
    assert verdict.details["incomplete_grounding"] == "dependency_graph_mismatch"


def test_attempt6_validation_surfaces_dependency_mismatch_as_redeclaration_signal() -> None:
    text = "查一下我的订单，再查下物流到哪了"
    goals = [_goal("g1", "查一下我的订单", []), _goal("g2", "再查下物流到哪了", ["g1"])]
    with patch("agent_core.lifecycle.goal_planning._goal_alignment_mode", return_value="model"), patch(
        "agent_core.config.get_model", return_value=object()
    ), patch("agent_core.model_calls.invoke_model", side_effect=_reaudit_calls()):
        result, declared = validate_goal_declaration(
            state={"current_user_input": text, "turn_index": 1},
            args={"goals": goals, "summary": "two independent outcomes"},
            capability_registry=None,
        )
    assert declared is None
    assert result["code"] == "GOAL_DECLARATION_INCOMPLETE"
    proof = result["data"]["alignment_proof"]
    assert proof["verdict"] == "incomplete"
    assert proof["reason_code"] == "goal_alignment_dependency_graph_mismatch"
    assert proof["details"]["dependency_graph_match"] is False
