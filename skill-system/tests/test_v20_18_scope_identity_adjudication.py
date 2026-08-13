from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services" / "agent-service" / "src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


def _response(payload: dict):
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(text: str, *, scope: str | None) -> dict:
    goal = {
        "goal_id": "g1",
        "description": text,
        "evidence_span": text,
        "requested_effect": {
            "domain": "order",
            "operation": "query",
            "object_type": "order",
            "requested_outputs": [{"output_id": "order.collection", "evidence_span": text}],
            "raw_description": text,
        },
        "expected_result_cardinality": "collection",
        "required": True,
        "depends_on": [],
    }
    if scope is not None:
        goal["target_candidate"] = {"scope_constraints": [{"evidence_span": scope}]}
    return goal


def _first_exact(text: str):
    return _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "outcome_preserved",
    })


def _blind_exact(text: str):
    return _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_decisions": [],
        "reason_code": "blind_audit_exact",
    })


def test_exact_blind_audit_cannot_silently_bless_target_identity_as_scope() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下某型号订单"
    scope = "某型号"
    third = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": [scope],
        "reason_code": "target_scope_constraint_fidelity",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[_first_exact(text), _blind_exact(text), third],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text, scope=scope)],
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == (scope,)
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_scope_constraint_adjudication"
    payload = invoke.call_args_list[2].kwargs["payload"][-1].content
    assert "Start each supplied entry from the assumption that it is NOT" in payload
    assert "Object identity, object/member naming" in payload


def test_real_population_filter_survives_same_scope_adjudication() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下满足条件的订单"
    scope = "满足条件"
    third = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "reason_code": "scope_constraint_is_population_narrowing",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[_first_exact(text), _blind_exact(text), third],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text, scope=scope)],
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_scope_constraint_adjudication"


def test_scope_free_goal_keeps_two_call_fast_path() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下订单"
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[_first_exact(text), _blind_exact(text)],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text, scope=None)],
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.exact


def test_declaration_protocol_excludes_identity_from_scope_contract() -> None:
    from agent_core.lifecycle.protocol import TARGET_CANDIDATE_SCHEMA

    description = str(TARGET_CANDIDATE_SCHEMA["description"])
    assert "只用于识别或选择目标的身份文字不是人口筛选" in description
    assert "禁止写入 scope_constraints" in description
