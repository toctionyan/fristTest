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


def _goal(goal_id: str, span: str, operation: str, *, target_candidate=None, reference_expression=None):
    row = {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {
            "domain": "record",
            "operation": operation,
            "object_type": "record",
            "raw_description": span,
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": [],
    }
    if target_candidate is not None:
        row["target_candidate"] = target_candidate
    if reference_expression is not None:
        row["reference_expression"] = reference_expression
    return row


def _independent_pair():
    return [{"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}]


def test_scope_claim_reaudit_preserves_prior_dependency_proof_even_if_final_model_drifts() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "List my records, then show their current summary"
    goals = [
        _goal("g1", "List my records", "list"),
        _goal("g2", "show their current summary", "query_summary"),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["List my records", "show their current summary"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    blind_scope_false_positive = _response({
        "verdict": "incomplete",
        "evidence_spans": ["List my records", "show their current summary"],
        "missing_spans": ["current summary"],
        "dependency_decisions": _independent_pair(),
        "reason_code": "target-scope-constraint coverage",
    })
    # Reproduce Attempt 3's failure mode: the semantic-only arbitration invents
    # a fresh dependency/basis even though the preceding blind proof was complete.
    # The repaired Runtime must ignore this field entirely rather than reopen graph authority.
    semantic_with_graph_drift = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "List my records",
        }],
        "reason_code": "scope_claim_withdrawn",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, blind_scope_false_positive, semantic_with_graph_drift],
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
    assert verdict.details["dependency_pair_decisions"] == _independent_pair()
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_scope_constraint_reaudit"
    final_messages = invoke.call_args_list[2].kwargs["payload"]
    final_text = "\n".join(str(getattr(message, "content", "") or "") for message in final_messages)
    assert "Do not re-judge, replace or return dependency_decisions" in final_text


def test_requested_effect_reaudit_preserves_empty_single_goal_dependency_proof() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "What is its current state?"
    goal = _goal(
        "g1",
        text,
        "query_details",
        reference_expression={
            "reference_type": "temporal_visible_result",
            "temporal_relation": "latest",
            "expected_cardinality": "single",
            "evidence_span": "its",
        },
    )
    first = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    false_effect_claim = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["current state"],
        "dependency_decisions": [],
        "reason_code": "requested-effect fidelity",
    })
    corrected = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "reason_code": "same_user_visible_effect_naming_granularity",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, false_effect_claim, corrected],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    final_messages = invoke.call_args_list[2].kwargs["payload"]
    final_text = "\n".join(str(getattr(message, "content", "") or "") for message in final_messages)
    assert "lexically broader or narrower" in final_text
    assert "Do not re-audit or return dependency_decisions" in final_text


def test_semantic_only_scope_reaudit_still_fails_closed_when_omission_is_confirmed() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Show records above the threshold"
    goal = _goal("g1", text, "list")
    first = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    mismatch = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["above the threshold"],
        "dependency_decisions": [],
        "reason_code": "target-scope-constraint coverage",
    })
    confirmed = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["above the threshold"],
        "reason_code": "target-scope-constraint coverage",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, mismatch, confirmed],
    ):
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[goal],
            known_tools=set(),
        )

    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("above the threshold",)
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True


def test_reference_expression_contract_requires_minimal_referring_subspan() -> None:
    from agent_core.lifecycle.protocol import REFERENCE_EXPRESSION_SCHEMA

    description = str(REFERENCE_EXPRESSION_SCHEMA["description"])
    assert "最小连续片段" in description
    assert "严格子串" in description
    assert "禁止为了凑满 Goal 证据" in description


def test_attempt3_repair_does_not_relax_positive_dependency_basis_grounding() -> None:
    from agent_core.lifecycle.goal_planning import _model_alignment_pairwise_dependency_proof

    text = "Load the records, then use that result"
    goals = [
        _goal("g1", "Load the records", "list"),
        _goal("g2", "use that result", "summarize"),
    ]
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text=text,
        goals=goals,
        values=[{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "Load the records",
        }],
    )
    assert error == "goal_alignment_dependency_basis_not_in_dependent_goal:0"
    assert details["dependency_proof_complete"] is False


def test_attempt3_repair_is_domain_neutral() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = source.index("preserved_blind_dependency_details")
    end = source.index("class CandidateOnlyGoalAlignmentVerifier", start)
    repair = source[start:end]
    for forbidden in ("快递员", "手机号", "鼠标", "物流", "蓝牙耳机", "退款"):
        assert forbidden not in repair
    assert "CapabilityRegistry" not in repair
    assert "dependency_proof_complete" in repair
    assert "dependency_graph_match" in repair
