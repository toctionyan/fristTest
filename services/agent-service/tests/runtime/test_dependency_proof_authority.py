from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from agent_core.lifecycle.goal_dependency_proof import (
    DependencyGraphObservation,
    DependencyProofMaturity,
    dependency_premise_digest,
    preserve_dependency_proof,
    reduce_dependency_graph_proof,
)
from agent_core.lifecycle.goal_planning import (
    ModelGoalAlignmentVerifier,
    _model_alignment_pairwise_dependency_proof,
)


def _observation(
    *,
    digest: str,
    edges: tuple[tuple[str, str], ...] = (),
    source: str = "candidate_blind_pairwise",
    complete: bool = True,
    observed_pair_count: int = 1,
) -> DependencyGraphObservation:
    return DependencyGraphObservation(
        premise_digest=digest,
        edges=edges,
        complete=complete,
        graph_matches_declaration=True,
        expected_pair_count=1,
        observed_pair_count=observed_pair_count,
        source=source,
    )


def test_complete_matching_absence_is_verified_not_authoritative() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1"))
    assert first.maturity == DependencyProofMaturity.VERIFIED
    assert first.preservable is True
    assert first.dependency_challenge_required is True
    assert first.authoritative is False


def test_second_dependency_challenge_closes_absence_authority() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1"))
    closed = reduce_dependency_graph_proof(first, _observation(digest="p1"))
    assert closed.maturity == DependencyProofMaturity.AUTHORITATIVE
    assert closed.edges == ()
    assert closed.dependency_challenge_required is False


def test_grounded_positive_challenge_can_replace_verified_absence() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1"))
    closed = reduce_dependency_graph_proof(
        first,
        _observation(digest="p1", edges=(("g2", "g1"),)),
    )
    assert closed.maturity == DependencyProofMaturity.AUTHORITATIVE
    assert closed.edges == (("g2", "g1"),)
    assert closed.reason_code == "adversarial_graph_changed_with_grounded_counterevidence"


def test_authoritative_graph_is_monotonic_under_repeated_same_premise_revotes() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1"))
    proof = reduce_dependency_graph_proof(
        first,
        _observation(digest="p1", edges=(("g2", "g1"),)),
    )
    assert proof.authoritative

    for index in range(20):
        attempted_edges = () if index % 2 == 0 else (("g1", "g2"),)
        proof = reduce_dependency_graph_proof(
            proof,
            _observation(digest="p1", edges=attempted_edges),
        )
        assert proof.authoritative
        assert proof.edges == (("g2", "g1"),)
        assert proof.reason_code == "authoritative_graph_revote_rejected"


def test_malformed_repeat_cannot_erase_preservable_proof() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1"))
    preserved = reduce_dependency_graph_proof(
        first,
        _observation(
            digest="p1",
            complete=False,
            observed_pair_count=0,
        ),
    )
    assert preserved.maturity == DependencyProofMaturity.VERIFIED
    assert preserved.preservable is True
    assert preserved.edges == first.edges
    assert preserved.reason_code == "inadmissible_observation_preserved_previous_proof"


def test_authoritative_graph_can_be_reproved_only_after_premise_change() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1"))
    closed = reduce_dependency_graph_proof(
        first,
        _observation(digest="p1", edges=(("g2", "g1"),)),
    )
    stale = preserve_dependency_proof(closed, premise_digest="p2")
    assert stale is not None
    assert stale.maturity == DependencyProofMaturity.STALE

    restarted = reduce_dependency_graph_proof(stale, _observation(digest="p2"))
    assert restarted.maturity == DependencyProofMaturity.VERIFIED
    assert restarted.edges == ()
    assert restarted.dependency_challenge_required is True


def test_dependency_premise_digest_excludes_planner_depends_on() -> None:
    base = [{
        "goal_id": "g1",
        "evidence_span": "inspect A",
        "requested_effect": {"domain": "open", "operation": "inspect", "object_type": "record"},
        "depends_on": [],
    }, {
        "goal_id": "g2",
        "evidence_span": "use that result",
        "requested_effect": {"domain": "open", "operation": "use", "object_type": "record"},
        "depends_on": [],
    }]
    changed = [dict(base[0]), {**base[1], "depends_on": ["g1"]}]
    assert dependency_premise_digest(user_text="inspect A then use that result", goals=base) == (
        dependency_premise_digest(user_text="inspect A then use that result", goals=changed)
    )


def _pairwise_goals(*, with_dependency: bool) -> list[dict]:
    return [
        {"goal_id": "g1", "evidence_span": "find the order", "depends_on": []},
        {
            "goal_id": "g2",
            "evidence_span": "assess that result",
            "depends_on": ["g1"] if with_dependency else [],
        },
    ]


def test_private_pairwise_parser_remains_stateless_across_calls() -> None:
    text = "find the order, then assess that result"
    details, error = _model_alignment_pairwise_dependency_proof(
        user_text=text,
        goals=_pairwise_goals(with_dependency=True),
        values=[{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "that result",
        }],
    )
    assert error is None
    assert details["dependency_proof_complete"] is True

    missing, missing_error = _model_alignment_pairwise_dependency_proof(
        user_text=text,
        goals=_pairwise_goals(with_dependency=False),
        values=[],
    )
    assert missing_error == "goal_alignment_dependency_pair_coverage_incomplete"
    assert missing["dependency_proof_complete"] is False
    assert missing["missing_dependency_pairs"] == [["g1", "g2"]]


def _response(payload: dict) -> tuple[SimpleNamespace, dict]:
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(
    goal_id: str,
    span: str,
    *,
    output_span: str,
    depends_on: list[str] | None = None,
) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {
            "domain": "open",
            "operation": goal_id,
            "object_type": "record",
            "raw_description": span,
            "requested_outputs": [{"output_id": goal_id, "evidence_span": output_span}],
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": list(depends_on or []),
    }


def test_release52_flow_closes_authority_only_after_adversarial_graph_challenge() -> None:
    text = "Inspect record A, then check whether that result is eligible"
    goals = [
        _goal("g1", "Inspect record A", output_span="Inspect record A"),
        _goal("g2", "check whether that result is eligible", output_span="is eligible"),
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
            "dependency_decisions": [{
                "goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent",
            }],
            "reason_code": "blind_empty_graph",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "check whether that result is eligible"],
            "missing_spans": [],
            "dependency_decisions": [{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "b_depends_on_a",
                "basis_kind": "result_reference",
                "basis_span": "that result",
            }],
            "reason_code": "adversarial_literal_result_reference",
        }),
    ]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ):
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert verdict.verdict == "incomplete"
    assert verdict.details["dependency_authority_state"] == "authoritative"
    assert verdict.details["dependency_observation_count"] == 2
    assert verdict.details["dependency_edges"] == [{
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "that result",
    }]


def test_release53_semantic_only_reaudit_preserves_verified_graph_without_revote() -> None:
    text = "Inspect record A, and summarize record B"
    goals = [
        _goal("g1", "Inspect record A", output_span="Inspect record A"),
        _goal("g2", "summarize record B", output_span="summarize record B"),
    ]
    independent = [{
        "goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent",
    }]
    calls = [
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "summarize record B"],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "candidate_exact",
        }),
        _response({
            "verdict": "incomplete",
            "evidence_spans": ["Inspect record A", "summarize record B"],
            "missing_spans": [],
            "dependency_decisions": independent,
            "reason_code": "semantic_coverage_gap",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "summarize record B"],
            "missing_spans": [],
            "reason_code": "withdraw_ungrounded_incomplete",
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
    assert verdict.details["dependency_authority_state"] == "verified"
    assert verdict.details["dependency_authority_preservable"] is True
    assert verdict.details["dependency_observation_count"] == 1
    assert verdict.details["dependency_edges"] == []
