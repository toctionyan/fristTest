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


def _goal(goal_id: str, span: str, effect: dict, *, depends_on: list[str] | None = None) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {**effect, "raw_description": span},
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": list(depends_on or []),
    }


def _independent_pair():
    return [{"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}]


def test_exact_blind_audit_with_sibling_effect_collision_gets_third_adjudication() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Inspect the record status, then provide the private handler contact"
    shared = {"domain": "record", "operation": "query_status", "object_type": "record"}
    goals = [
        _goal("g1", "Inspect the record status", shared),
        _goal("g2", "provide the private handler contact", shared),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect the record status", "provide the private handler contact"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    blind_exact = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect the record status", "provide the private handler contact"],
        "missing_spans": [],
        "dependency_decisions": _independent_pair(),
        "reason_code": "blind_exact_but_effects_not_challenged",
    })
    adversarial = _response({
        "verdict": "incomplete",
        "evidence_spans": ["Inspect the record status", "provide the private handler contact"],
        "missing_spans": ["private handler contact"],
        "dependency_decisions": _independent_pair(),
        "reason_code": "requested_effect_fidelity_collision",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind_exact, adversarial]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("private handler contact",)
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_effect_collision_adjudication"
    third_payload = str(invoke.call_args_list[2].kwargs["payload"])
    assert "REQUESTED_EFFECT_COLLISION_RISK" in third_payload
    assert "capability_registry_consulted" in third_payload
    assert '"depends_on"' not in third_payload


def test_legitimate_same_effect_siblings_survive_collision_adjudication() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Inspect record A status and inspect record B status"
    shared = {"domain": "record", "operation": "query_status", "object_type": "record"}
    goals = [
        _goal("g1", "Inspect record A status", shared),
        _goal("g2", "inspect record B status", shared),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect record A status", "inspect record B status"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect record A status", "inspect record B status"],
        "missing_spans": [],
        "dependency_decisions": _independent_pair(),
        "reason_code": "blind_exact",
    })
    confirmed = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect record A status", "inspect record B status"],
        "missing_spans": [],
        "dependency_decisions": _independent_pair(),
        "reason_code": "same_effect_is_faithful_for_both_targets",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind, confirmed]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_effect_collision_adjudication"


def test_second_blind_inventory_adversarially_prunes_meta_deferral() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    text = "先不办理。那无线鼠标什么时候发货？"
    goals = [{"goal_id": "g1", "evidence_span": "无线鼠标什么时候发货", "depends_on": []}]
    first = _response({
        "verdict": "exact",
        "outcome_spans": ["先不办理", "无线鼠标什么时候发货"],
        "reason_code": "first_pass_treated_deferral_as_outcome",
    })
    second = _response({
        "verdict": "exact",
        "outcome_spans": ["无线鼠标什么时候发货"],
        "reason_code": "adversarial_control_reaudit_query_only",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, second]
    ) as invoke:
        verdict = ModelGoalGranularityVerifier().verify(
            user_text=text,
            goals=goals,
            active_structured_interaction=None,
        )

    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["blind_self_audit_attempted"] is True
    second_payload = str(invoke.call_args_list[1].kwargs["payload"])
    assert "FIRST_BLIND_OUTCOME_SPANS" in second_payload
    assert "not authority" in second_payload
    assert "unsupported/open business effect" in second_payload


def test_true_unsupported_sibling_is_not_pruned_by_second_blind_audit() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    text = "Inspect the record status, then provide the private handler contact"
    goals = [{"goal_id": "g1", "evidence_span": "Inspect the record status", "depends_on": []}]
    first = _response({
        "verdict": "exact",
        "outcome_spans": ["Inspect the record status", "provide the private handler contact"],
        "reason_code": "two_business_outcomes",
    })
    second = _response({
        "verdict": "exact",
        "outcome_spans": ["Inspect the record status", "provide the private handler contact"],
        "reason_code": "unsupported_sibling_remains_outcome",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, second]
    ):
        verdict = ModelGoalGranularityVerifier().verify(
            user_text=text,
            goals=goals,
            active_structured_interaction=None,
        )

    assert verdict.verdict == "under_split"
    assert verdict.details["inventory_outcome_count"] == 2
    assert verdict.details["matched_outcome_count"] == 1


def test_pending_interaction_cancellation_plus_query_remains_two_outcomes() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    text = "Stop this pending request, then inspect record B status"
    goals = [
        {"goal_id": "g1", "evidence_span": "Stop this pending request", "depends_on": []},
        {"goal_id": "g2", "evidence_span": "inspect record B status", "depends_on": []},
    ]
    active = {
        "interaction_id": "interaction:request:1",
        "lifecycle": "pending",
        "title": "Pending request",
        "target": "record A",
        "required_fields": [],
        "chat_write_authorized": False,
        "runtime_redirect_required": True,
    }
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        return_value=_response({
            "verdict": "exact",
            "outcome_spans": ["Stop this pending request", "inspect record B status"],
            "reason_code": "pending_control_and_read_query",
        }),
    ):
        verdict = ModelGoalGranularityVerifier().verify(
            user_text=text,
            goals=goals,
            active_structured_interaction=active,
        )

    assert verdict.exact
    assert verdict.details["inventory_outcome_count"] == 2


def test_attempt5_production_repairs_remain_domain_neutral() -> None:
    planning = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    granularity = (AGENT_SRC / "agent_core/lifecycle/goal_granularity.py").read_text(encoding="utf-8")
    start = planning.index("def _requested_effect_sibling_collision_risk")
    end = planning.index("def _literal_role_overlap", start)
    policy = planning[start:end]
    assert "capability_registry_consulted" in policy
    assert "runtime_rejection_authority" in policy
    assert "REQUESTED_EFFECT_COLLISION_RISK" in planning
    assert "FIRST_BLIND_OUTCOME_SPANS" in granularity
    for forbidden in ("快递员", "手机号", "鼠标", "物流", "退款"):
        assert forbidden not in policy
