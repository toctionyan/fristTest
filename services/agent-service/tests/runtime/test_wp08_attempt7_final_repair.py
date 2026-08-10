from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from agent_core.lifecycle.dialogue_runtime import _workflow_repair_tools
from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier


def _response(payload: dict) -> tuple[SimpleNamespace, dict]:
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "depends_on": depends_on,
        "requested_effect": {
            "domain": "order" if goal_id == "g1" else "refund",
            "operation": "list" if goal_id == "g1" else "create",
            "object_type": "order",
        },
    }


def test_attempt7_dependency_reaudit_rejects_shared_scope_false_edge() -> None:
    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [_goal("g1", "查一下鼠标订单", []), _goal("g2", "帮我申请退款", ["g1"])]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_value_input",
            "basis_span": "帮我申请退款",
        }],
        "reason_code": "exact",
    })
    second = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "independent_dependency_audit_exact",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, second]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["verifier_repair_kind"] == "dependency_independent_reaudit"
    rendered = "\n".join(
        str(getattr(message, "content", message))
        for message in invoke.call_args_list[1].kwargs["payload"]
    )
    assert '"depends_on": ["g1"]' not in rendered
    assert "candidate_dependency_graph_hidden" in rendered


def test_attempt7_dependency_reaudit_preserves_true_result_reference_edge() -> None:
    text = "查一下键盘订单，再看看它能不能退款"
    goals = [
        _goal("g1", "查一下键盘订单", []),
        {
            **_goal("g2", "它能不能退款", ["g1"]),
            "requested_effect": {"domain": "refund", "operation": "assess_eligibility", "object_type": "order"},
        },
    ]
    payload = {
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "它能不能退款"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }],
        "reason_code": "exact",
    }
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[_response(payload), _response(payload)]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.verdict == "exact"
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "dependency_independent_reaudit"


class _Registry:
    def contract_for_tool(self, name: str):
        if name == "report_unsupported_request":
            return SimpleNamespace(execution_kind="unsupported")
        if name == "get_order_details":
            return SimpleNamespace(execution_kind="grounding_read")
        return None


def test_attempt7_workflow_repair_keeps_current_policy_support_frontier() -> None:
    surface = {"goals": [{
        "goal_id": "g1",
        "completion_tools": [],
        "candidate_tools": ["get_order_details"],
        "status": "matched",
    }]}
    policy = {"goal_policies": [
        {"goal_id": "g1", "allowed_tools": ["get_order_details", "report_unsupported_request"]},
        {"goal_id": "other", "allowed_tools": ["list_orders"]},
    ]}
    with patch(
        "agent_core.lifecycle.dialogue_runtime.read_plan_projection",
        return_value={"goals": [
            {"goal_id": "g1", "required": True, "coverage_status": "PENDING"},
            {"goal_id": "done", "required": True, "coverage_status": "COMPLETE"},
        ]},
    ):
        pending, repair_tools, unsupported = _workflow_repair_tools(
            {}, _Registry(), surface, pretool_execution_policy=policy
        )
    assert pending == {"g1"}
    assert repair_tools == {"get_order_details"}
    assert unsupported == set()
    assert "list_orders" not in repair_tools
    assert "report_unsupported_request" not in repair_tools


def test_workflow_repair_preserves_exact_completion_tools_without_policy() -> None:
    surface = {"goals": [{
        "goal_id": "g1",
        "completion_tools": ["get_order_details"],
        "candidate_tools": ["get_order_details"],
        "status": "matched",
    }]}
    with patch(
        "agent_core.lifecycle.dialogue_runtime.read_plan_projection",
        return_value={"goals": [{"goal_id": "g1", "required": True, "coverage_status": "PENDING"}]},
    ):
        pending, repair_tools, unsupported = _workflow_repair_tools({}, _Registry(), surface)
    assert pending == {"g1"}
    assert repair_tools == {"get_order_details"}
    assert unsupported == set()
