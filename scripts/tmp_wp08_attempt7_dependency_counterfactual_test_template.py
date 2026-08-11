from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services" / "agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for value in (AGENT_ROOT, AGENT_SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))


def _response(payload: dict):
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(goal_id: str, span: str, *, effect: tuple[str, str, str], depends_on: list[str] | None = None) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {
            "domain": effect[0],
            "operation": effect[1],
            "object_type": effect[2],
            "raw_description": span,
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": list(depends_on or []),
    }


def _edge(basis_span: str, basis_kind: str = "result_value_input") -> dict:
    return {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": basis_kind,
        "basis_span": basis_span,
    }


def _pair_positive(basis_span: str, basis_kind: str = "result_value_input") -> list[dict]:
    return [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "b_depends_on_a",
        "basis_kind": basis_kind,
        "basis_span": basis_span,
    }]


def _pair_independent() -> list[dict]:
    return [{"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}]


def test_surviving_spurious_nonreference_edge_gets_counterfactual_fourth_audit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Inspect record A, then open a service request for record A"
    goals = [
        _goal("g1", "Inspect record A", effect=("record", "query", "record")),
        _goal("g2", "open a service request for record A", effect=("service", "create_request", "request"), depends_on=["g1"]),
    ]
    calls = [
        _response({"verdict": "exact", "evidence_spans": ["Inspect record A", "open a service request for record A"], "missing_spans": [], "dependency_edges": [_edge("record A")], "reason_code": "candidate_support_flow_confusion"}),
        _response({"verdict": "exact", "evidence_spans": ["Inspect record A", "open a service request for record A"], "missing_spans": [], "dependency_decisions": _pair_positive("record A"), "reason_code": "blind_support_flow_confusion"}),
        _response({"verdict": "exact", "evidence_spans": ["Inspect record A", "open a service request for record A"], "missing_spans": [], "dependency_decisions": _pair_positive("record A"), "reason_code": "adversarial_still_anchored_on_execution"}),
        _response({"verdict": "exact", "evidence_spans": ["Inspect record A", "open a service request for record A"], "missing_spans": [], "dependency_decisions": _pair_independent(), "reason_code": "counterfactual_result_removal_proves_independence"}),
    ]
    with patch("agent_core.config.get_model", return_value=object()), patch("agent_core.model_calls.invoke_model", side_effect=calls) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())

    assert invoke.call_count == 4
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_positive_edge_counterfactual"
    fourth_request = json.loads(invoke.call_args_list[3].kwargs["payload"][-1].content)
    assert "earlier Goal has produced no result payload" in fourth_request["FORMAT_REPAIR"]
    assert "stable-ID/artifact lookup" in fourth_request["FORMAT_REPAIR"]
    projected = fourth_request["DECLARED_GOALS"]
    assert len(projected) == 2
    assert all(set(row) == {"goal_id", "evidence_span"} for row in projected)


def test_attempt7_exact_failure_shape_is_rejected_when_result_payload_is_not_consumed() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [
        _goal("g1", "查一下鼠标订单", effect=("order", "list", "order")),
        _goal("g2", "帮我申请退款", effect=("refund", "create", "order"), depends_on=["g1"]),
    ]
    positive = _pair_positive("帮我申请退款")
    calls = [
        _response({"verdict": "exact", "evidence_spans": ["查一下鼠标订单", "帮我申请退款"], "missing_spans": [], "dependency_edges": [_edge("帮我申请退款")], "reason_code": "candidate_declared_edge"}),
        _response({"verdict": "exact", "evidence_spans": ["查一下鼠标订单", "帮我申请退款"], "missing_spans": [], "dependency_decisions": positive, "reason_code": "blind_declared_edge"}),
        _response({"verdict": "exact", "evidence_spans": ["查一下鼠标订单", "帮我申请退款"], "missing_spans": [], "dependency_decisions": positive, "reason_code": "adversarial_declared_edge"}),
        _response({"verdict": "exact", "evidence_spans": ["查一下鼠标订单", "帮我申请退款"], "missing_spans": [], "dependency_decisions": _pair_independent(), "reason_code": "literal_same_turn_target_survives_result_removal"}),
    ]
    with patch("agent_core.config.get_model", return_value=object()), patch("agent_core.model_calls.invoke_model", side_effect=calls):
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())

    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.details["dependency_edges"] == []


def test_explicit_result_reference_closes_after_existing_adversarial_audit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Inspect record A, then use that result to open a service request"
    goals = [
        _goal("g1", "Inspect record A", effect=("record", "query", "record")),
        _goal("g2", "use that result to open a service request", effect=("service", "create_request", "request"), depends_on=["g1"]),
    ]
    edge = _edge("that result", "result_reference")
    positive = _pair_positive("that result", "result_reference")
    calls = [
        _response({"verdict": "exact", "evidence_spans": ["Inspect record A", "use that result to open a service request"], "missing_spans": [], "dependency_edges": [edge], "reason_code": "true_result_reference"}),
        _response({"verdict": "exact", "evidence_spans": ["Inspect record A", "use that result to open a service request"], "missing_spans": [], "dependency_decisions": positive, "reason_code": "blind_true_result_reference"}),
        _response({"verdict": "exact", "evidence_spans": ["Inspect record A", "use that result to open a service request"], "missing_spans": [], "dependency_decisions": positive, "reason_code": "adversarial_true_result_reference"}),
    ]
    with patch("agent_core.config.get_model", return_value=object()), patch("agent_core.model_calls.invoke_model", side_effect=calls) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"][0]["basis_span"] == "that result"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_positive_edge_adjudication"


def test_semantic_live_bundle_budget_includes_four_alignment_slots() -> None:
    source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
    assert "1 declaration + 4 alignment + 2 granularity" in source
    assert 'model_call_scope(max_calls=168, scope="preprod_semantic_goal_prototypes")' in source


def test_attempt7_counterfactual_production_repair_is_domain_neutral() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = source.index('verifier_repair_kind = "candidate_blind_dependency_positive_edge_counterfactual"')
    end = source.index("normalized_semantic_reason = (", start)
    section = source[start:end]
    assert "result payload" in section
    assert "same-turn zero-anaphora" in section
    assert "stable-ID/artifact lookup" in section
    assert "Do not infer tool order" in section
    for forbidden in ("鼠标", "物流", "退款", "快递员", "手机号"):
        assert forbidden not in section
