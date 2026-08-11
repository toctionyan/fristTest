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


def _goal(goal_id: str, span: str, effect: dict) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {**effect, "raw_description": span},
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": [],
    }


def _pair():
    return [{"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}]


def test_requested_effect_reaudit_cannot_erase_sibling_effect_collision() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Fetch the account status, then provide the private handler contact"
    effect = {"domain": "record", "operation": "query_status", "object_type": "record"}
    goals = [
        _goal("g1", "Fetch the account status", effect),
        _goal("g2", "provide the private handler contact", effect),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["Fetch the account status", "provide the private handler contact"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    mismatch = _response({
        "verdict": "incomplete",
        "evidence_spans": ["Fetch the account status", "provide the private handler contact"],
        "missing_spans": ["private handler contact"],
        "dependency_decisions": _pair(),
        "reason_code": "requested_effect_not_faithful_to_business_effect",
    })
    unsafe_withdrawal = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_decisions": _pair(),
        "reason_code": "naming_granularity_only",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, mismatch, unsafe_withdrawal],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "requested_effect_reaudit_structural_collision"
    assert verdict.missing_spans == ("private handler contact",)
    guard = verdict.details["requested_effect_reaudit_guard"]
    assert guard["risk"] is True
    assert guard["capability_registry_consulted"] is False
    assert guard["language_interpretation_used"] is False
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_requested_effect_reaudit"


def test_requested_effect_collision_guard_is_inactive_for_unique_open_identity() -> None:
    from agent_core.lifecycle.goal_planning import _requested_effect_reaudit_collision_guard

    goals = [
        _goal("g1", "Fetch the account status", {"domain": "record", "operation": "query_status", "object_type": "record"}),
        _goal("g2", "provide the private handler contact", {"domain": "support", "operation": "get_handler_contact", "object_type": "handler"}),
    ]
    guard = _requested_effect_reaudit_collision_guard(goals, ("private handler contact",))
    assert guard["risk"] is False
    assert guard["collisions"] == []


def test_reference_span_cannot_also_be_scope_constraint() -> None:
    from agent_core.lifecycle.goal_planning import _scope_constraint_role_conflict_errors

    goals = [{
        "goal_id": "g1",
        "target_candidate": {"scope_constraints": [{"evidence_span": "that result"}]},
        "reference_expression": {"evidence_span": "that result"},
    }]
    errors = _scope_constraint_role_conflict_errors(goals, user_text="Can that result be refunded?")
    assert errors == ["scope_constraint_conflicts_with_reference_expression:g1:0"]


def test_literal_execution_commitment_cannot_also_be_scope_constraint() -> None:
    from agent_core.lifecycle.goal_planning import _scope_constraint_role_conflict_errors

    goals = [{
        "goal_id": "g1",
        "target_candidate": {"scope_constraints": [{"evidence_span": "do not submit"}]},
        "execution_commitment": "do not submit",
    }]
    errors = _scope_constraint_role_conflict_errors(
        goals,
        user_text="Check eligibility but do not submit anything",
    )
    assert errors == ["scope_constraint_conflicts_with_execution_commitment:g1:0"]


def test_reference_and_real_population_filter_can_coexist_when_roles_do_not_overlap() -> None:
    from agent_core.lifecycle.goal_planning import _scope_constraint_role_conflict_errors

    goals = [{
        "goal_id": "g1",
        "target_candidate": {"scope_constraints": [{"evidence_span": "over 100"}]},
        "reference_expression": {"evidence_span": "those records"},
    }]
    assert _scope_constraint_role_conflict_errors(
        goals,
        user_text="Of those records, show the ones over 100",
    ) == []


def test_attempt2_repair_is_domain_neutral_and_keeps_capability_registry_out() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = source.index("def _requested_effect_reaudit_collision_guard")
    end = source.index("class ModelGoalAlignmentVerifier", start)
    guard = source[start:end]
    assert "CapabilityRegistry" not in guard
    assert "capability_registry." not in guard
    assert "language_interpretation_used" in guard
    blind_start = source.index("blind_dependency_instruction =")
    blind_end = source.index("prompt = {", blind_start)
    policy = source[blind_start:blind_end]
    assert "every supplied scope_constraints entry" in policy
    assert "execution commitment" in policy
    for forbidden in ("快递员", "手机号", "鼠标", "物流", "退款"):
        assert forbidden not in guard
