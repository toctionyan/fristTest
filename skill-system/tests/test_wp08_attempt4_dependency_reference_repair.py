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


def _goal(goal_id: str, span: str, *, depends_on=None, reference=None, proof=None):
    row = {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {
            "domain": "record",
            "operation": "query",
            "object_type": "record",
            "raw_description": span,
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": list(depends_on or []),
    }
    if reference is not None:
        row["reference_expression"] = reference
    if proof is not None:
        row["referent_resolution_proof"] = proof
    return row


def _positive_decision(*, basis_span: str):
    return [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "b_depends_on_a",
        "basis_kind": "result_reference",
        "basis_span": basis_span,
    }]


def test_matching_positive_dependency_requires_adversarial_third_audit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Load the records, then show the summary"
    goals = [
        _goal("g1", "Load the records"),
        _goal("g2", "show the summary", depends_on=["g1"]),
    ]
    candidate = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "show the summary"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "summary",
        }],
        "reason_code": "candidate_positive",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "show the summary"],
        "missing_spans": [],
        "dependency_decisions": _positive_decision(basis_span="summary"),
        "reason_code": "blind_positive",
    })
    adversarial = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "show the summary"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "independent",
        }],
        "reason_code": "support_flow_not_result_dependency",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[candidate, blind, adversarial]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_positive_edge_adjudication"
    third_payload = repr(invoke.call_args_list[2].kwargs.get("payload"))
    assert "Start every unordered Goal pair from independent" in third_payload
    assert "'depends_on'" not in third_payload


def test_true_positive_dependency_survives_adversarial_third_audit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Load the records, then use that result"
    goals = [
        _goal("g1", "Load the records"),
        _goal("g2", "use that result", depends_on=["g1"]),
    ]
    edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "that result",
    }
    candidate = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "use that result"],
        "missing_spans": [],
        "dependency_edges": [edge],
        "reason_code": "true_reference",
    })
    decision = [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "b_depends_on_a",
        "basis_kind": "result_reference",
        "basis_span": "that result",
    }]
    blind = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "use that result"],
        "missing_spans": [],
        "dependency_decisions": decision,
        "reason_code": "blind_true_reference",
    })
    adversarial = _response({
        "verdict": "exact",
        "evidence_spans": ["Load the records", "use that result"],
        "missing_spans": [],
        "dependency_decisions": decision,
        "reason_code": "adversarial_true_reference",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[candidate, blind, adversarial]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"][0]["basis_span"] == "that result"


def test_unique_historical_reference_indeterminate_gets_candidate_blind_reaudit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "What is its current state?"
    reference = {
        "reference_type": "temporal_visible_result",
        "temporal_relation": "latest",
        "expected_cardinality": "single",
        "evidence_span": "its",
    }
    goal = _goal(
        "g1",
        text,
        reference=reference,
        proof={"resolution_status": "UNIQUE"},
    )
    ungrounded = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_reference_exact_but_ungrounded",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_decisions": [],
        "reason_code": "historical_reference_faithful",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[ungrounded, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
            recent_public_context=[{"answer_summary": "One record was shown", "historical_only": True}],
        )

    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_historical_reference_reaudit"
    second_payload = repr(invoke.call_args_list[1].kwargs.get("payload"))
    assert "reference_expression" in second_payload
    assert "strict subspan" in second_payload
    assert "'depends_on'" not in second_payload


def test_historical_reference_reaudit_remains_fail_closed_on_real_mismatch() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "What is its current state?"
    goal = _goal(
        "g1",
        text,
        reference={
            "reference_type": "temporal_visible_result",
            "temporal_relation": "latest",
            "expected_cardinality": "single",
            "evidence_span": "its",
        },
        proof={"resolution_status": "UNIQUE"},
    )
    ungrounded = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_reference_exact_but_ungrounded",
    })
    mismatch = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["current state"],
        "dependency_decisions": [],
        "reason_code": "requested_effect_fidelity",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[ungrounded, mismatch]
    ):
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
        )

    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("current state",)


def test_attempt4_repair_is_domain_neutral_and_does_not_rewrite_dependencies() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = source.index("candidate_blind_dependency_positive_edge_adjudication")
    end = source.index("normalized_semantic_reason", start)
    repair = source[start:end]
    for forbidden in ("快递员", "手机号", "鼠标", "物流", "蓝牙耳机", "退款"):
        assert forbidden not in repair
    assert "capability_registry" not in repair
    assert "Start every unordered " in repair
    assert "Goal pair from independent" in repair
    assert "Do not see or reconstruct Planner depends_on" in repair
