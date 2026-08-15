from __future__ import annotations

import inspect
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier


def _response(payload: dict) -> tuple[SimpleNamespace, dict]:
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(
    goal_id: str,
    span: str,
    *,
    output_id: str,
    output_span: str,
    depends_on: list[str] | None = None,
) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {
            "domain": "open",
            "operation": output_id,
            "object_type": "record",
            "raw_description": span,
            "requested_outputs": [{
                "output_id": output_id,
                "evidence_span": output_span,
            }],
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": list(depends_on or []),
    }


def _independent_pair() -> list[dict]:
    return [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "independent",
    }]


def _positive_pair() -> list[dict]:
    return [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "b_depends_on_a",
        "basis_kind": "result_reference",
        "basis_span": "that result",
    }]


def test_release52_empty_dependency_graph_receives_third_adversarial_challenge() -> None:
    text = "Inspect record A, then check whether that result is eligible"
    goals = [
        _goal("g1", "Inspect record A", output_id="semantic.one", output_span="Inspect record A"),
        _goal(
            "g2",
            "check whether that result is eligible",
            output_id="semantic.two",
            output_span="is eligible",
        ),
    ]
    calls = [
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "check whether that result is eligible"],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "candidate_empty_graph",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "check whether that result is eligible"],
            "missing_spans": [],
            "dependency_decisions": _independent_pair(),
            "reason_code": "blind_empty_graph",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "check whether that result is eligible"],
            "missing_spans": [],
            "dependency_decisions": _positive_pair(),
            "reason_code": "adversarial_literal_result_reference",
        }),
    ]

    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
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
    assert verdict.details["dependency_edges"] == [{
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "that result",
    }]
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_independence_adjudication"

    third_request = json.loads(invoke.call_args_list[2].kwargs["payload"][-1].content)
    assert "dependency absence is not settled" in third_request["FORMAT_REPAIR"]
    assert all("depends_on" not in row for row in third_request["DECLARED_GOALS"])


def test_release52_true_independent_siblings_remain_exact_after_third_challenge() -> None:
    text = "Inspect record A, and summarize record B"
    goals = [
        _goal("g1", "Inspect record A", output_id="semantic.one", output_span="Inspect record A"),
        _goal("g2", "summarize record B", output_id="semantic.two", output_span="summarize record B"),
    ]
    calls = [
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "summarize record B"],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "candidate_independent",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "summarize record B"],
            "missing_spans": [],
            "dependency_decisions": _independent_pair(),
            "reason_code": "blind_independent",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "summarize record B"],
            "missing_spans": [],
            "dependency_decisions": _independent_pair(),
            "reason_code": "adversarial_independent",
        }),
    ]

    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_independence_adjudication"


def test_release52_independence_adjudication_runtime_branch_is_domain_neutral() -> None:
    source = inspect.getsource(ModelGoalAlignmentVerifier.verify)
    start = source.index('verifier_repair_kind = "candidate_blind_dependency_independence_adjudication"')
    end = source.index('elif scope_constraint_risk["risk"]:', start)
    section = source[start:end]
    lowered = section.casefold()
    for forbidden in ("键盘", "退款", "订单", "鼠标", "物流"):
        assert forbidden not in lowered
    for forbidden in ("invoice", "refund", "order"):
        assert re.search(rf"\b{re.escape(forbidden)}\b", lowered) is None
