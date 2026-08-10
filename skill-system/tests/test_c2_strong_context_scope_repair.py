from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services/agent-service/src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


def _condition_goal() -> dict:
    return {
        "goal_id": "g1",
        "condition": {
            "version": "condition-expression@1",
            "op": "eq",
            "left": {"source": "target_fact", "path": "delivery_status"},
            "right": {"source": "literal", "value": "运输中"},
        },
        "requested_effect": {
            "domain": "logistics",
            "operation": "query",
            "object_type": "order",
        },
    }


def test_formal_goal_condition_rejects_empty_candidate_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import capability_gate

    monkeypatch.setattr(capability_gate, "semantic_goals", lambda _state: [_condition_goal()])
    proof = capability_gate._formal_goal_condition_coverage_proof(
        {},
        goal_ids={"g1"},
        parameterization={"bindings": [], "parameterization_complete": True, "errors": []},
    )

    assert proof["required"] is True
    assert proof["complete"] is False
    assert proof["requirements"] == [{
        "goal_id": "g1",
        "operand_source": "target_fact",
        "condition_path": "delivery_status",
        "parameter_leaf": "delivery_status",
    }]
    assert proof["errors"] == ["formal_goal_condition_unbound:g1:delivery_status"]


def test_formal_goal_condition_accepts_exact_formal_argument_leaf(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import capability_gate

    monkeypatch.setattr(capability_gate, "semantic_goals", lambda _state: [_condition_goal()])
    proof = capability_gate._formal_goal_condition_coverage_proof(
        {},
        goal_ids={"g1"},
        parameterization={
            "bindings": [{
                "kind": "condition",
                "source_span": "哪些还在路上",
                "parameter_path": "query.delivery_status",
                "normalized_value": "运输中",
                "actual_value": "运输中",
                "status": "covered",
            }],
            "parameterization_complete": True,
            "errors": [],
        },
    )

    assert proof["complete"] is True
    assert proof["checks"][0]["matched_parameter_path"] == "query.delivery_status"


def test_goal_output_condition_is_workflow_dependency_not_tool_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import capability_gate

    goal = {
        "goal_id": "g2",
        "condition": {
            "version": "condition-expression@1",
            "op": "eq",
            "left": {"source": "goal_output", "goal_id": "g1", "path": "eligible"},
            "right": {"source": "literal", "value": True},
        },
    }
    monkeypatch.setattr(capability_gate, "semantic_goals", lambda _state: [goal])
    proof = capability_gate._formal_goal_condition_coverage_proof(
        {}, goal_ids={"g2"}, parameterization={"bindings": []}
    )
    assert proof["required"] is False
    assert proof["complete"] is True


def test_answer_release_rejects_conditioned_result_without_backend_execution_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import answer_release_alignment as alignment

    proof = {
        "candidate_tool": "get_order_logistics",
        "parameterization_complete": True,
        "formal_goal_condition_coverage": {
            "required": True,
            "complete": True,
            "requirements": [{"condition_path": "delivery_status"}],
        },
    }
    monkeypatch.setattr(alignment, "_formal_goals", lambda _result: [])
    monkeypatch.setattr(alignment, "_effective_match_proofs", lambda _result: [proof])
    monkeypatch.setattr(alignment, "_runtime_evidence", lambda _result: [])

    verdict = alignment._deterministic_verdict(result={}, blocks=[])
    assert verdict.decision == "reject"
    assert verdict.reason_code == "required_condition_execution_evidence_missing"
    assert verdict.details["tools"] == ["get_order_logistics"]


def test_answer_release_accepts_condition_only_after_backend_proves_same_condition(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import answer_release_alignment as alignment

    proof = {
        "candidate_tool": "get_order_logistics",
        "parameterization_complete": True,
        "formal_goal_condition_coverage": {"required": True, "complete": True},
    }
    evidence = [{
        "evidence_kind": "current_tool_parameterization",
        "tool_name": "get_order_logistics",
        "ok": True,
        "parameterization": {
            "required_backend_conditions": {"delivery_status": "运输中"},
            "backend_applied_conditions": {"delivery_status": "运输中"},
            "source_population_count": 4,
            "matched_population_count": 1,
            "presentation_population": "matched_members",
        },
    }]
    monkeypatch.setattr(alignment, "_formal_goals", lambda _result: [])
    monkeypatch.setattr(alignment, "_effective_match_proofs", lambda _result: [proof])
    monkeypatch.setattr(alignment, "_runtime_evidence", lambda _result: evidence)

    verdict = alignment._deterministic_verdict(
        result={},
        blocks=[{"contract_id": "commerce.logistics_overview@1", "items": [{}]}],
    )
    assert verdict.decision == "pass"
    assert verdict.reason_code == "deterministic_evidence_complete"


def test_exact_scope_fast_path_cannot_bypass_incomplete_formal_condition(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import answer_release_alignment as alignment

    proof = {
        "candidate_tool": "get_order_logistics",
        "exact_match": True,
        "parameterization_complete": True,
        "formal_goal_condition_coverage": {"required": True, "complete": False},
        "visible_result_reference": {"complete": True},
        "explicit_member_scope": {"complete": True},
        "derived_collection_scope": {"complete": True},
        "semantic_verdict": {"verdict": "exact"},
        "constraint_errors": [],
    }
    result = {
        "runtime_outcome": {"outcome_type": "query", "evidence_handles": ["h_result:broad"]},
        "tool_trace": [{"classification": "observation", "result": {"ok": True}}],
    }
    monkeypatch.setattr(alignment, "_effective_match_proofs", lambda _result: [proof])
    monkeypatch.setattr(alignment, "validate_visible_result_ref", lambda **_kwargs: ({"result_ref": "h_result:broad"}, None))
    monkeypatch.setattr(alignment, "_formal_goals", lambda _result: [])

    assert alignment._deterministic_release_authority(result) is None


def test_attempt8_regression_contract_is_structural_not_phrase_heuristic() -> None:
    capability_source = (AGENT_SRC / "agent_core/runtime/capability_gate.py").read_text(encoding="utf-8")
    answer_source = (AGENT_SRC / "agent_core/runtime/answer_release_alignment.py").read_text(encoding="utf-8")

    assert "formal_goal_condition_coverage" in capability_source
    assert "condition_operands" in capability_source
    assert "required_condition_execution_evidence_missing" in answer_source
    assert "哪些还在路上" not in capability_source
    assert "在路上" not in answer_source
