from __future__ import annotations

import inspect
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier


def _response(payload: dict) -> tuple[SimpleNamespace, dict]:
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(goal_id: str, span: str, *, output_id: str) -> dict:
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
                "evidence_span": span,
            }],
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": [],
    }


def _goals() -> list[dict]:
    return [
        _goal("g1", "Inspect record A", output_id="semantic.one"),
        _goal("g2", "summarize record B", output_id="semantic.two"),
    ]


def _independent_pair() -> list[dict]:
    return [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "independent",
    }]


def _first_exact() -> tuple[SimpleNamespace, dict]:
    return _response({
        "verdict": "exact",
        "evidence_spans": ["Inspect record A", "summarize record B"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_exact",
    })


def _blind_ungrounded_incomplete() -> tuple[SimpleNamespace, dict]:
    return _response({
        "verdict": "incomplete",
        "evidence_spans": ["Inspect record A", "summarize record B"],
        "missing_spans": [],
        "dependency_decisions": _independent_pair(),
        "reason_code": "semantic_coverage_gap",
    })


def test_release53_blind_ungrounded_incomplete_gets_claim_only_then_dependency_closure() -> None:
    text = "Inspect record A, and summarize record B"
    calls = [
        _first_exact(),
        _blind_ungrounded_incomplete(),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "summarize record B"],
            "missing_spans": [],
            "reason_code": "withdraw_ungrounded_incomplete",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "summarize record B"],
            "missing_spans": [],
            "dependency_decisions": _independent_pair(),
            "reason_code": "dependency_authority_closed_independent",
        }),
    ]

    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=_goals(),
            known_tools=set(),
        )

    assert invoke.call_count == 4
    assert verdict.exact
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["dependency_authority_state"] == "authoritative"
    assert verdict.details["dependency_challenge_required"] is False
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_authority_closure"

    third_request = json.loads(invoke.call_args_list[2].kwargs["payload"][-1].content)
    assert all("depends_on" not in row for row in third_request["DECLARED_GOALS"])
    assert "Do not re-audit or return dependency_decisions" in third_request["FORMAT_REPAIR"]

    fourth_request = json.loads(invoke.call_args_list[3].kwargs["payload"][-1].content)
    assert all("depends_on" not in row for row in fourth_request["DECLARED_GOALS"])
    assert "complete/matching alone is not authority" in fourth_request["FORMAT_REPAIR"]


def test_release53_grounded_blind_semantic_mismatch_remains_incomplete() -> None:
    text = "Inspect record A, and summarize record B"
    calls = [
        _first_exact(),
        _blind_ungrounded_incomplete(),
        _response({
            "verdict": "incomplete",
            "evidence_spans": ["Inspect record A"],
            "missing_spans": ["summarize record B"],
            "reason_code": "requested_effect_fidelity",
        }),
    ]

    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=_goals(),
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("summarize record B",)
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["dependency_authority_state"] == "verified"


def test_release53_final_ungrounded_negative_claim_still_fails_closed_without_extra_retry() -> None:
    text = "Inspect record A, and summarize record B"
    calls = [
        _first_exact(),
        _blind_ungrounded_incomplete(),
        _response({
            "verdict": "incomplete",
            "evidence_spans": ["Inspect record A", "summarize record B"],
            "missing_spans": [],
            "reason_code": "semantic_coverage_gap_again",
        }),
    ]

    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=_goals(),
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_alignment_missing_span_not_grounded"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_authority_state"] == "verified"


def test_release53_semantic_grounding_adjudication_branch_is_domain_neutral() -> None:
    source = inspect.getsource(ModelGoalAlignmentVerifier.verify)
    start = source.index('verifier_repair_kind = "candidate_blind_dependency_semantic_grounding_adjudication"')
    end = source.index('if verdict.verdict in {"exact", "incomplete"}:', start)
    section = source[start:end].casefold()
    for forbidden in ("键盘", "退款", "订单", "鼠标", "物流"):
        assert forbidden not in section
    for forbidden in ("invoice", "refund", "order"):
        assert re.search(rf"\b{re.escape(forbidden)}\b", section) is None
