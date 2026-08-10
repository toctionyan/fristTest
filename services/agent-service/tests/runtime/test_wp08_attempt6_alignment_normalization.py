from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from agent_core.lifecycle.goal_planning import (
    GoalAlignmentVerdict,
    _as_alignment_verdict,
    verify_goal_alignment,
)


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {
        "goal_id": goal_id,
        "evidence_span": span,
        "depends_on": depends_on,
    }


def _response(payload: dict) -> tuple[SimpleNamespace, dict]:
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def test_attempt6_dependency_mismatch_survives_outer_alignment_normalization() -> None:
    """Reproduce the Attempt-6 false GOAL_ALIGNMENT_UNVERIFIED conversion."""
    text = "查一下我的订单，再查下物流到哪了"
    goals = [
        _goal("g1", "查一下我的订单", []),
        _goal("g2", "再查下物流到哪了", ["g1"]),
    ]
    verifier_response = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下我的订单", "再查下物流到哪了"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "declared_dependency_not_expressed",
    })

    with patch(
        "agent_core.lifecycle.goal_planning._goal_alignment_mode",
        return_value="model",
    ), patch(
        "agent_core.config.get_model",
        return_value=object(),
    ), patch(
        "agent_core.model_calls.invoke_model",
        return_value=verifier_response,
    ) as invoke:
        verdict = verify_goal_alignment(
            state={"current_user_input": text},
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 1
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
    assert verdict.missing_spans == ()
    assert verdict.details["dependency_authority"] == "independent_goal_alignment"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is False
    assert verdict.details["dependency_edges"] == []


def test_grounded_dependency_mismatch_normalization_is_idempotent() -> None:
    text = "查一下我的订单，再查下物流到哪了"
    original = GoalAlignmentVerdict(
        "incomplete",
        ("查一下我的订单", "再查下物流到哪了"),
        (),
        "goal_alignment_dependency_graph_mismatch",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": False,
            "dependency_edges": [],
        },
    )

    normalized = _as_alignment_verdict(
        original,
        user_text=text,
        source="model",
        independent=True,
    )

    assert normalized == original


def test_incomplete_without_missing_span_or_dependency_proof_still_fails_closed() -> None:
    text = "查一下我的订单，再查下物流到哪了"
    ungrounded = GoalAlignmentVerdict(
        "incomplete",
        ("查一下我的订单",),
        (),
        "some_ungrounded_incomplete_claim",
        "model",
        True,
        {},
    )

    normalized = _as_alignment_verdict(
        ungrounded,
        user_text=text,
        source="model",
        independent=True,
    )

    assert normalized.verdict == "indeterminate"
    assert normalized.reason_code == "goal_alignment_missing_span_not_grounded"
    assert normalized.details["original_verdict"] == "incomplete"
    assert normalized.details["grounding_failure"] == "missing_spans"
