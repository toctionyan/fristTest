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


def test_open_unsupported_effect_false_positive_gets_bounded_reaudit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下鼠标物流，再告诉我快递员手机号"
    goals = [
        _goal(
            "g1",
            "查一下鼠标物流",
            {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        ),
        _goal(
            "g2",
            "再告诉我快递员手机号",
            {"domain": "delivery", "operation": "get_courier_phone", "object_type": "courier"},
        ),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    false_effect_claim = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": ["快递员手机号"],
        "dependency_decisions": _pair(),
        "reason_code": "requested-effect fidelity",
    })
    corrected = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_decisions": _pair(),
        "reason_code": "open_requested_effect_preserves_user_visible_outcome",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, false_effect_claim, corrected],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.evidence_spans == ("查一下鼠标物流", "再告诉我快递员手机号")
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_requested_effect_reaudit"
    repair_message = invoke.call_args_list[2].kwargs["payload"][-1].content
    assert "open semantic identity" in repair_message
    assert "capability availability must not be used as evidence" in repair_message
    assert "remain incomplete" in repair_message


def test_real_effect_substitution_remains_fail_closed_after_reaudit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下鼠标物流，再告诉我快递员手机号"
    goals = [
        _goal(
            "g1",
            "查一下鼠标物流",
            {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        ),
        _goal(
            "g2",
            "再告诉我快递员手机号",
            {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        ),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    mismatch = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": ["快递员手机号"],
        "dependency_decisions": _pair(),
        "reason_code": "requested_effect_not_faithful_to_business_effect",
    })
    confirmed = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": ["快递员手机号"],
        "dependency_decisions": _pair(),
        "reason_code": "requested_effect_fidelity",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, mismatch, confirmed],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("快递员手机号",)
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_requested_effect_reaudit"


def test_unrelated_incomplete_claim_does_not_gain_requested_effect_reaudit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下还在路上的订单"
    goals = [
        _goal(
            "g1",
            text,
            {"domain": "order", "operation": "list", "object_type": "order"},
        )
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    scope = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["还在路上"],
        "dependency_decisions": [],
        "reason_code": "target-scope-constraint coverage",
    })
    confirmed_scope = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["还在路上"],
        "dependency_decisions": [],
        "reason_code": "target-scope-constraint coverage",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, scope, confirmed_scope],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_scope_constraint_reaudit"


def test_requested_effect_reaudit_policy_is_domain_neutral() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = source.index('verifier_repair_kind = "candidate_blind_dependency_requested_effect_reaudit"')
    end = source.index('normalized_scope_reason = normalized_semantic_reason', start)
    policy = source[start:end]
    assert "open semantic identity" in policy
    assert "capability availability must not be used as " in policy
    assert "consult a capability registry" in policy
    for forbidden in ("快递员", "手机号", "鼠标", "物流"):
        assert forbidden not in policy
