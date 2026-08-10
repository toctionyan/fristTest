from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "services" / "agent-service"
SRC = AGENT / "src"
for value in (AGENT, SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))


def _response(payload: dict):
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {"goal_id": goal_id, "evidence_span": span, "depends_on": depends_on}


def test_attempt4_refund_scope_recovers_only_after_grounded_blind_format_retry() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [_goal("g1", "查一下鼠标订单", []), _goal("g2", "帮我申请退款", [])]
    first_false_positive = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "帮我申请退款",
        }],
        "reason_code": "execution_prerequisite_confused_with_result_dependency",
    })
    malformed_blind = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "查一下鼠标订单",
        }],
        "reason_code": "bad_blind_basis",
    })
    grounded_independent = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "independent",
        }],
        "reason_code": "shared_scope_is_not_result_dependency",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first_false_positive, malformed_blind, grounded_independent],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_format_repair"
    third = str(invoke.call_args_list[2].kwargs["payload"])
    assert '"depends_on"' not in third
    assert "structural grounding contract" in third


def test_true_literal_result_reference_does_not_need_third_retry() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", ["g1"])]
    edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_edges": [edge],
        "reason_code": "literal_result_reference",
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
        "reason_code": "literal_result_reference_confirmed",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_edges"][0]["basis_span"] == "它"


def test_goal_inventory_receives_pending_interaction_context_and_excludes_meta_deferral() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    text = "先不办理。那无线鼠标什么时候发货？"
    goals = [_goal("g1", "无线鼠标什么时候发货", [])]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        return_value=_response({
            "verdict": "exact",
            "outcome_spans": ["无线鼠标什么时候发货"],
            "reason_code": "deferral_is_interaction_control_query_is_outcome",
        }),
    ) as invoke:
        verdict = ModelGoalGranularityVerifier().verify(
            user_text=text,
            goals=goals,
            active_structured_interaction=None,
        )
    assert verdict.exact
    payload = str(invoke.call_args.kwargs["payload"])
    assert "ACTIVE_STRUCTURED_INTERACTION" in payload
    assert "meta-level refusal" in payload
    assert "direct business-effect request" in payload


def test_pending_interaction_can_still_be_an_explicit_control_outcome() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    text = "把这个申请停掉，再查无线鼠标物流"
    goals = [_goal("g1", "把这个申请停掉", []), _goal("g2", "查无线鼠标物流", [])]
    active = {
        "interaction_id": "interaction:refund:1",
        "lifecycle": "pending",
        "title": "退款申请",
        "target": "订单10001",
        "required_fields": [],
        "chat_write_authorized": False,
        "runtime_redirect_required": True,
    }
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        return_value=_response({
            "verdict": "exact",
            "outcome_spans": ["把这个申请停掉", "查无线鼠标物流"],
            "reason_code": "pending_control_plus_read_query",
        }),
    ):
        verdict = ModelGoalGranularityVerifier().verify(
            user_text=text,
            goals=goals,
            active_structured_interaction=active,
        )
    assert verdict.exact
    assert verdict.details["inventory_outcome_count"] == 2


def test_frozen_inventory_authority_is_bound_to_interaction_snapshot() -> None:
    from agent_core.lifecycle.goal_granularity import (
        _build_inventory_authority,
        _validate_inventory_authority,
    )

    text = "先不办理。那无线鼠标什么时候发货？"
    authority = _build_inventory_authority(
        user_text=text,
        outcome_spans=("无线鼠标什么时候发货",),
        reason_code="query_only",
        blind_self_audit_attempted=False,
        active_structured_interaction=None,
    )
    valid, spans, error = _validate_inventory_authority(
        user_text=text,
        authority=authority,
        active_structured_interaction=None,
    )
    assert error is None
    assert valid is not None
    assert spans == ("无线鼠标什么时候发货",)
    changed = {"interaction_id": "interaction:new", "lifecycle": "pending"}
    invalid, _, changed_error = _validate_inventory_authority(
        user_text=text,
        authority=authority,
        active_structured_interaction=changed,
    )
    assert invalid is None
    assert changed_error == "goal_granularity_inventory_authority_interaction_mismatch"
