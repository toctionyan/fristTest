from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from agent_core.lifecycle.dialogue_runtime import _workflow_repair_allowed_tools
from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {
            "domain": "order" if goal_id == "g1" else "refund",
            "operation": "query" if goal_id == "g1" else "create",
            "object_type": "order",
        },
        "expected_result_cardinality": "single",
        "depends_on": depends_on,
    }


def _response(payload: dict) -> tuple[SimpleNamespace, dict]:
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _invoke_payload_text(mock_call) -> str:
    return repr(mock_call.kwargs.get("payload"))


def test_attempt7_false_dependency_requires_candidate_blind_second_audit() -> None:
    """A candidate-aware verifier may anchor on the same false edge as Planner."""
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [
        _goal("g1", "查一下鼠标订单", []),
        _goal("g2", "帮我申请退款", ["g1"]),
    ]
    false_edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_value_input",
        "basis_span": "申请退款",
    }
    responses = [
        _response({
            "verdict": "exact",
            "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
            "missing_spans": [],
            "dependency_edges": [false_edge],
            "reason_code": "candidate_anchored_false_exact",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
            "missing_spans": [],
            "dependency_decisions": [{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "independent",
            }],
            "reason_code": "blind_shared_scope_independent",
        }),
    ]

    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=responses
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.missing_spans == ()
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"
    second_payload = _invoke_payload_text(invoke.call_args_list[1])
    assert "'depends_on'" not in second_payload
    assert '"depends_on"' not in second_payload


def test_candidate_blind_second_audit_preserves_true_result_dependency() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [
        _goal("g1", "查一下键盘订单", []),
        _goal("g2", "再看看它能不能退款", ["g1"]),
    ]
    true_edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }
    candidate = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_edges": [true_edge],
        "reason_code": "true_result_reference",
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
        "reason_code": "true_result_reference",
    })

    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[candidate, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"][0]["basis_span"] == "它"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"


def test_candidate_blind_second_audit_detects_missing_true_dependency() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [
        _goal("g1", "查一下键盘订单", []),
        _goal("g2", "再看看它能不能退款", []),
    ]
    true_edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }
    responses = [
        _response({
            "verdict": "exact",
            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "candidate_anchored_missing_edge",
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
            "reason_code": "blind_true_result_reference",
        }),
    ]

    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=responses
    ):
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["dependency_edges"][0]["basis_span"] == "它"


def test_workflow_completion_retry_keeps_policy_bounded_support_frontier() -> None:
    """A rejected target candidate must be repairable without exposing terminals."""
    allowed = _workflow_repair_allowed_tools(
        policy_frontier={"get_order_details"},
        completion_tools=set(),
        unsupported_tools=set(),
    )

    assert "get_order_details" in allowed
    assert "ask_user_clarification" in allowed
    assert "respond_to_user" not in allowed


def test_workflow_completion_retry_never_widens_beyond_supplied_exact_sets() -> None:
    allowed = _workflow_repair_allowed_tools(
        policy_frontier={"get_order_details"},
        completion_tools={"prepare_refund"},
        unsupported_tools={"report_unsupported_request"},
    )

    assert allowed == {
        "get_order_details",
        "prepare_refund",
        "report_unsupported_request",
        "ask_user_clarification",
    }


def test_workflow_completion_retry_clarification_only_does_not_inherit_policy_frontier() -> None:
    allowed = _workflow_repair_allowed_tools(
        policy_frontier={"get_order_details", "report_unsupported_request"},
        completion_tools=set(),
        unsupported_tools={"report_unsupported_request"},
        clarification_only=True,
    )

    assert allowed == {"ask_user_clarification"}
