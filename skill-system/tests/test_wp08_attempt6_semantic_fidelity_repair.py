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


def _goal(goal_id: str, span: str, effect: dict, *, target_candidate=None, condition=None) -> dict:
    row = {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": effect,
        "expected_result_cardinality": "collection",
        "required": True,
        "depends_on": [],
    }
    if target_candidate is not None:
        row["target_candidate"] = target_candidate
    if condition is not None:
        row["condition"] = condition
    return row


def test_single_goal_exact_is_reaudited_for_omitted_scope_constraint() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "哪些还在路上？"
    goal = _goal(
        "g1",
        text,
        {"domain": "order", "operation": "query_logistics", "object_type": "order"},
    )
    first = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "first_pass_exact",
    })
    blind = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["在路上"],
        "dependency_decisions": [],
        "reason_code": "explicit_scope_constraint_not_structured",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("在路上",)
    assert verdict.reason_code == "explicit_scope_constraint_not_structured"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"
    audit_messages = invoke.call_args_list[1].kwargs["payload"]
    audit_text = "\n".join(str(getattr(message, "content", "") or "") for message in audit_messages)
    assert "requested_effect" in audit_text
    assert "scope_constraints" in audit_text
    assert "target-member selection" in audit_text
    assert "dependency_decisions" in audit_text


def test_near_capability_effect_coercion_is_rejected_without_runtime_keyword_rules() -> None:
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
            # Reproduce Attempt 6's wrong nearby registered effect.
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
    blind = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": ["快递员手机号"],
        "dependency_decisions": [
            {"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}
        ],
        "reason_code": "requested_effect_not_faithful_to_business_effect",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("快递员手机号",)
    assert verdict.reason_code == "requested_effect_not_faithful_to_business_effect"
    assert verdict.details["dependency_pair_decisions"] == [
        {"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}
    ]


def test_single_goal_with_structured_scope_constraint_can_pass_same_reaudit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "哪些还在路上？"
    goal = _goal(
        "g1",
        text,
        {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        target_candidate={"scope_constraints": [{"evidence_span": "在路上"}]},
    )
    first = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "first_pass_exact",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_decisions": [],
        "reason_code": "semantic_fields_and_dependency_exact",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.evidence_spans == (text,)
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"


def test_blind_projection_hides_candidate_dependency_but_keeps_semantic_fields() -> None:
    from agent_core.lifecycle.goal_planning import _dependency_blind_goal_projection

    target_candidate = {"scope_constraints": [{"evidence_span": "在路上"}]}
    projected = _dependency_blind_goal_projection([
        {
            "goal_id": "g1",
            "evidence_span": "在路上",
            "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
            "target_candidate": target_candidate,
            "expected_result_cardinality": "collection",
            "required": True,
            "depends_on": ["g0"],
        }
    ])[0]
    assert "depends_on" not in projected
    assert projected["target_candidate"] == target_candidate
    assert projected["requested_effect"]["operation"] == "query_logistics"


def test_reaudit_policy_is_domain_neutral_and_does_not_hardcode_attempt6_phrases() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = source.index("blind_dependency_instruction =")
    end = source.index("prompt = {", start)
    policy = source[start:end]
    assert "requested_effect" in policy
    assert "scope_constraints" in policy
    assert "unsupported/open" in policy
    assert "快递员" not in policy
    assert "在路上" not in policy
    assert "运输中" not in policy


def test_existing_capability_gate_still_requires_frozen_condition_binding() -> None:
    source = (AGENT_SRC / "agent_core/runtime/capability_gate.py").read_text(encoding="utf-8")
    assert "def _formal_goal_condition_coverage_proof" in source
    assert "def _formal_goal_scope_coverage_proof" in source
    assert "formal_goal_condition_unbound" in source
    assert "parameterized_query_missing_constraint_binding" in source
