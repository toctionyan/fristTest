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


def _goal(text: str) -> dict:
    return {
        "goal_id": "g1",
        "description": "判断退货退款资格",
        "evidence_span": text,
        "requested_effect": {
            "domain": "after_sales",
            "operation": "check_return_refund_eligibility",
            "object_type": "order",
            "raw_description": text,
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": [],
    }


def test_attempt5_ambiguous_target_reaudit_can_freeze_outcome_without_inventing_target() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "可以退货退款吗？"
    first = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["具体订单"],
        "dependency_edges": [],
        "reason_code": "target_not_selected",
    })
    second = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "requested_outcome_preserved_target_selection_downstream",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, second]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text)],
            known_tools=set(),
            recent_public_context=[{
                "turn": 1,
                "user_summary": "我买过什么？",
                "answer_summary": "展示了四笔订单",
                "result_handles": ["h_result:orders"],
                "historical_only": True,
            }],
        )

    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "incomplete_claim_grounding_reaudit"
    repair_message = invoke.call_args_list[1].kwargs["payload"][-1].content
    assert "dependency_edges" in repair_message
    assert "single Goal" in repair_message
    assert "target-resolution step" in repair_message


def test_grounding_reaudit_still_fails_closed_when_dependency_graph_field_is_omitted() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "可以退货退款吗？"
    first = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["具体订单"],
        "dependency_edges": [],
        "reason_code": "target_not_selected",
    })
    malformed_exact = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "reason_code": "exact_but_dependency_graph_omitted",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, malformed_exact, malformed_exact],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text)],
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_alignment_dependency_edges_required"


def test_exact_claim_grounding_reaudit_also_preserves_complete_dependency_contract() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "可以退货退款吗？"
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["退货退款资格"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "ungrounded_exact",
    })
    second = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "grounded_exact",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, second]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text)],
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    repair_message = invoke.call_args_list[1].kwargs["payload"][-1].content
    assert "dependency_edges" in repair_message
    assert "single Goal" in repair_message


def test_target_member_selection_remains_post_freeze_runtime_concern() -> None:
    goal_source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    dialogue_source = (AGENT_SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
    protocol_source = (AGENT_SRC / "agent_core/lifecycle/protocol.py").read_text(encoding="utf-8")

    assert "target-member selection" in goal_source
    assert "downstream Runtime concerns" in goal_source
    assert "_clarification_terminal_goal_ids" in dialogue_source
    assert '"missing_kind": {"type": "string", "enum": ["target", "scope", "condition", "intent"]}' in protocol_source
    assert "target_not_selected" not in goal_source
    assert "可以退货退款吗" not in goal_source
