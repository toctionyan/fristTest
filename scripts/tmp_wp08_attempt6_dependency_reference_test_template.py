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


def _goal(
    goal_id: str,
    span: str,
    *,
    effect: tuple[str, str, str],
    depends_on: list[str] | None = None,
    target_candidate: dict | None = None,
    reference_expression: dict | None = None,
) -> dict:
    row = {
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
    if target_candidate is not None:
        row["target_candidate"] = target_candidate
    if reference_expression is not None:
        row["reference_expression"] = reference_expression
    return row


def _positive_pair(basis_span: str) -> list[dict]:
    return [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "b_depends_on_a",
        "basis_kind": "result_value_input",
        "basis_span": basis_span,
    }]


def test_positive_dependency_adjudication_uses_minimal_candidate_blind_projection() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Inspect record A, then open a service request"
    goals = [
        _goal(
            "g1",
            "Inspect record A",
            effect=("record", "query", "record"),
            target_candidate={"description": "record A", "scope_constraints": []},
        ),
        _goal(
            "g2",
            "open a service request",
            effect=("service", "create_request", "request"),
            depends_on=["g1"],
            target_candidate={"description": "record A resolved by support lookup"},
        ),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect record A", "open a service request"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_value_input",
            "basis_span": "service request",
        }],
        "reason_code": "candidate_support_flow_confusion",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect record A", "open a service request"],
        "missing_spans": [],
        "dependency_decisions": _positive_pair("service request"),
        "reason_code": "blind_support_flow_confusion",
    })
    adversarial = _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect record A", "open a service request"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "independent",
        }],
        "reason_code": "same_turn_literal_target_is_support_only",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind, adversarial]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.details["dependency_edges"] == []
    third_messages = invoke.call_args_list[2].kwargs["payload"]
    third_request = json.loads(third_messages[-1].content)
    assert "zero-anaphora ellipsis" in third_request["FORMAT_REPAIR"]
    projected = third_request["DECLARED_GOALS"]
    assert len(projected) == 2
    assert all(set(row) == {"goal_id", "evidence_span"} for row in projected)
    assert all("target_candidate" not in row for row in projected)
    assert all("requested_effect" not in row for row in projected)
    assert all("reference_expression" not in row for row in projected)
    assert all("depends_on" not in row for row in projected)


def test_true_result_dependency_survives_minimal_adjudication() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Inspect record A, then use that result"
    goals = [
        _goal("g1", "Inspect record A", effect=("record", "query", "record")),
        _goal(
            "g2",
            "use that result",
            effect=("service", "create_request", "request"),
            depends_on=["g1"],
        ),
    ]
    first_edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "that result",
    }
    decision = [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "b_depends_on_a",
        "basis_kind": "result_reference",
        "basis_span": "that result",
    }]
    calls = [
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "use that result"],
            "missing_spans": [],
            "dependency_edges": [first_edge],
            "reason_code": "true_reference",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "use that result"],
            "missing_spans": [],
            "dependency_decisions": decision,
            "reason_code": "blind_true_reference",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "use that result"],
            "missing_spans": [],
            "dependency_decisions": decision,
            "reason_code": "adversarial_true_reference",
        }),
    ]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ):
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())

    assert verdict.exact
    assert verdict.details["dependency_edges"][0]["basis_span"] == "that result"


def test_recent_public_context_includes_bounded_visible_member_labels() -> None:
    from agent_core.lifecycle.goal_planning import _recent_public_context

    state = {
        "artifact_ledger": [{"placeholder": True}],
        "conversation_event_log": [],
    }
    visible = [{
        "source_turn": 1,
        "result_ref": "result:old",
        "shape": "collection",
        "member_handles": ["artifact:record:B"],
        "member_labels": ["Record B"],
        "resource_types": ["record"],
    }]
    with patch(
        "agent_core.lifecycle.goal_planning.visible_result_refs_from_ledger",
        return_value=visible,
    ) as projected:
        rows = _recent_public_context(state)

    projected.assert_called_once()
    assert rows == [{
        "context_kind": "visible_result_ref",
        "turn": 1,
        "result_ref": "result:old",
        "shape": "collection",
        "member_handles": ["artifact:record:B"],
        "member_labels": ["Record B"],
        "resource_types": ["record"],
        "historical_only": True,
        "semantic_target_authority": False,
    }]


def test_candidate_blind_alignment_rejects_omitted_historical_reference_semantics() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Back to Record B, what is its status?"
    goals = [
        _goal(
            "g1",
            text,
            effect=("record", "query_status", "record"),
            target_candidate={"description": "Record B"},
        )
    ]
    candidate = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_missed_historical_relation",
    })
    blind = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["Record B"],
        "dependency_decisions": [],
        "reason_code": "historical_reference_omitted",
    })
    context = [{
        "context_kind": "visible_result_ref",
        "turn": 1,
        "result_ref": "result:old",
        "shape": "collection",
        "member_handles": ["artifact:record:B"],
        "member_labels": ["Record B"],
        "resource_types": ["record"],
        "historical_only": True,
        "semantic_target_authority": False,
    }]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[candidate, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
            recent_public_context=context,
        )

    assert invoke.call_count == 2
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("Record B",)
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"
    second_payload = repr(invoke.call_args_list[1].kwargs["payload"])
    assert "reference_expression is required" in second_payload
    assert "member_labels" in second_payload


def test_label_overlap_alone_does_not_deterministically_force_historical_reference() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Inspect Record B"
    goals = [_goal("g1", text, effect=("record", "query", "record"))]
    exact = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "fresh_literal_target",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_decisions": [],
        "reason_code": "fresh_literal_target_not_historical",
    })
    context = [{
        "context_kind": "visible_result_ref",
        "turn": 1,
        "result_ref": "result:old",
        "shape": "collection",
        "member_handles": ["artifact:record:B"],
        "member_labels": ["Record B"],
        "resource_types": ["record"],
        "historical_only": True,
        "semantic_target_authority": False,
    }]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[exact, blind]
    ):
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
            recent_public_context=context,
        )

    assert verdict.exact


def test_dialogue_prompt_gives_historical_label_precedence_over_fresh_entity_match_escape() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
    marker = "fresh literal target 不要求对象先出现在 visible_result_refs"
    assert marker in source
    start = source.index(marker)
    section = source[start:start + 900]
    assert "reference_expression" in section
    assert "entity_match" in section
    assert "不得用 entity_match 绕过历史引用证明" in section
    assert "Runtime 不做关键词或名称自动绑定" in section


def test_attempt6_repair_source_remains_domain_neutral() -> None:
    planning = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = planning.index("def _dependency_adjudication_goal_projection")
    end = planning.index("def _has_unique_historical_reference", start)
    helper = planning[start:end]
    assert "target candidates" in helper
    assert "Runtime never rewrites" in helper
    for forbidden in ("鼠标", "物流", "退款", "快递员", "手机号"):
        assert forbidden not in helper
