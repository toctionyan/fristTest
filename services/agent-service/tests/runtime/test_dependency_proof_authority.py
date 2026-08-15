from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from agent_core.lifecycle.goal_dependency_proof import (
    DependencyGraphObservation,
    DependencyObservationRole,
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
    source: str = "candidate_blind_dependency_reaudit",
    role: DependencyObservationRole = DependencyObservationRole.PROVISIONAL,
    complete: bool = True,
    observed_pair_count: int = 1,
    evidence: str = "e1",
    supersedes: str | None = None,
) -> DependencyGraphObservation:
    return DependencyGraphObservation(
        premise_digest=digest,
        edges=edges,
        complete=complete,
        graph_matches_declaration=True,
        expected_pair_count=1,
        observed_pair_count=observed_pair_count,
        source=source,
        role=role,
        evidence_digest=evidence,
        supersedes_evidence_digest=supersedes,
    )


def test_complete_matching_absence_is_verified_not_authoritative() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1"))
    assert first.maturity == DependencyProofMaturity.VERIFIED
    assert first.preservable is True
    assert first.dependency_challenge_required is True
    assert first.authoritative is False


def test_second_provisional_observation_does_not_mint_authority() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1", evidence="blind-1"))
    second = reduce_dependency_graph_proof(
        first,
        _observation(digest="p1", evidence="blind-2"),
    )
    assert second.maturity == DependencyProofMaturity.VERIFIED
    assert second.authoritative is False
    assert second.dependency_challenge_required is True
    assert second.reason_code == "provisional_reaudit_does_not_mint_authority"


def test_explicit_dependency_closure_closes_absence_authority() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1", evidence="blind"))
    closed = reduce_dependency_graph_proof(
        first,
        _observation(
            digest="p1",
            evidence="closure",
            role=DependencyObservationRole.ADVERSARIAL_CLOSURE,
            source="candidate_blind_dependency_independence_adjudication",
        ),
    )
    assert closed.maturity == DependencyProofMaturity.AUTHORITATIVE
    assert closed.edges == ()
    assert closed.dependency_challenge_required is False
    assert closed.authority_evidence_digest == "closure"


def test_grounded_positive_closure_can_replace_verified_absence() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1", evidence="blind"))
    closed = reduce_dependency_graph_proof(
        first,
        _observation(
            digest="p1",
            edges=(("g2", "g1"),),
            role=DependencyObservationRole.ADVERSARIAL_CLOSURE,
            source="candidate_blind_dependency_independence_adjudication",
            evidence="positive-closure",
        ),
    )
    assert closed.maturity == DependencyProofMaturity.AUTHORITATIVE
    assert closed.edges == (("g2", "g1"),)
    assert closed.reason_code == "adversarial_graph_changed_with_grounded_counterevidence"


def test_authoritative_graph_is_monotonic_under_repeated_same_premise_revotes() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1", evidence="blind"))
    proof = reduce_dependency_graph_proof(
        first,
        _observation(
            digest="p1",
            edges=(("g2", "g1"),),
            role=DependencyObservationRole.ADVERSARIAL_CLOSURE,
            evidence="closure",
        ),
    )
    assert proof.authoritative

    for index in range(20):
        attempted_edges = () if index % 2 == 0 else (("g1", "g2"),)
        proof = reduce_dependency_graph_proof(
            proof,
            _observation(
                digest="p1",
                edges=attempted_edges,
                role=DependencyObservationRole.ADVERSARIAL_CLOSURE,
                evidence=f"revote-{index}",
            ),
        )
        assert proof.authoritative
        assert proof.edges == (("g2", "g1"),)
        assert proof.reason_code == "unbound_authoritative_graph_revote_rejected"


def test_unbound_counterevidence_cannot_downgrade_authority() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1", evidence="blind"))
    proof = reduce_dependency_graph_proof(
        first,
        _observation(
            digest="p1",
            edges=(("g2", "g1"),),
            role=DependencyObservationRole.ADVERSARIAL_CLOSURE,
            evidence="authority",
        ),
    )
    attempted = reduce_dependency_graph_proof(
        proof,
        _observation(
            digest="p1",
            edges=(),
            role=DependencyObservationRole.COUNTEREVIDENCE,
            evidence="new-negative",
        ),
    )
    assert attempted.authoritative
    assert attempted.edges == (("g2", "g1"),)
    assert attempted.reason_code == "counterevidence_not_bound_to_current_authority"


def test_bound_new_counterevidence_requires_reclosure_before_authority_can_flip() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1", evidence="blind"))
    authority = reduce_dependency_graph_proof(
        first,
        _observation(
            digest="p1",
            edges=(("g2", "g1"),),
            role=DependencyObservationRole.ADVERSARIAL_CLOSURE,
            evidence="authority",
        ),
    )
    challenged = reduce_dependency_graph_proof(
        authority,
        _observation(
            digest="p1",
            edges=(),
            role=DependencyObservationRole.COUNTEREVIDENCE,
            evidence="counter-1",
            supersedes=authority.authority_evidence_digest,
        ),
    )
    assert challenged.maturity == DependencyProofMaturity.CHALLENGED
    assert challenged.authoritative is False
    assert challenged.challenge_edges == ()

    still_challenged = reduce_dependency_graph_proof(
        challenged,
        _observation(
            digest="p1",
            edges=(),
            role=DependencyObservationRole.RECLOSURE,
            evidence="counter-1",
        ),
    )
    assert still_challenged.maturity == DependencyProofMaturity.CHALLENGED

    flipped = reduce_dependency_graph_proof(
        still_challenged,
        _observation(
            digest="p1",
            edges=(),
            role=DependencyObservationRole.RECLOSURE,
            evidence="counter-2",
        ),
    )
    assert flipped.maturity == DependencyProofMaturity.AUTHORITATIVE
    assert flipped.edges == ()
    assert flipped.reason_code == "challenge_reclosed_with_distinct_evidence"


def test_malformed_repeat_cannot_erase_preservable_proof() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1"))
    preserved = reduce_dependency_graph_proof(
        first,
        _observation(
            digest="p1",
            complete=False,
            observed_pair_count=0,
            evidence="",
        ),
    )
    assert preserved.maturity == DependencyProofMaturity.VERIFIED
    assert preserved.preservable is True
    assert preserved.edges == first.edges
    assert preserved.reason_code == "inadmissible_observation_preserved_previous_proof"


def test_authoritative_graph_can_be_reproved_only_after_premise_change() -> None:
    first = reduce_dependency_graph_proof(None, _observation(digest="p1", evidence="blind"))
    closed = reduce_dependency_graph_proof(
        first,
        _observation(
            digest="p1",
            edges=(("g2", "g1"),),
            role=DependencyObservationRole.ADVERSARIAL_CLOSURE,
            evidence="closure",
        ),
    )
    stale = preserve_dependency_proof(closed, premise_digest="p2")
    assert stale is not None
    assert stale.maturity == DependencyProofMaturity.STALE

    restarted = reduce_dependency_graph_proof(stale, _observation(digest="p2", evidence="new-premise"))
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


def test_release43_missing_true_dependency_closes_authority_before_repair_feedback() -> None:
    text = "Inspect record A, then assess that result"
    goals = [
        _goal("g1", "Inspect record A", output_span="Inspect record A"),
        _goal("g2", "assess that result", output_span="assess", depends_on=[]),
    ]
    positive = [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "b_depends_on_a",
        "basis_kind": "result_reference",
        "basis_span": "that result",
    }]
    calls = [
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "assess that result"],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "candidate_missing_edge",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "assess that result"],
            "missing_spans": [],
            "dependency_decisions": positive,
            "reason_code": "blind_found_true_edge",
        }),
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "assess that result"],
            "missing_spans": [],
            "dependency_decisions": positive,
            "reason_code": "closure_confirmed_true_edge",
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
    assert verdict.details["dependency_authority_state"] == "authoritative"
    assert verdict.details["dependency_challenge_required"] is False
    assert verdict.details["dependency_edges"][0]["basis_span"] == "that result"


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
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.details["dependency_authority_state"] == "authoritative"
    assert verdict.details["dependency_observation_count"] == 2
    assert verdict.details["dependency_edges"] == [{
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "that result",
    }]


def test_release53_semantic_only_reaudit_preserves_graph_then_dedicated_closure_finishes_authority() -> None:
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
        _response({
            "verdict": "exact",
            "evidence_spans": ["Inspect record A", "summarize record B"],
            "missing_spans": [],
            "dependency_decisions": independent,
            "reason_code": "dependency_authority_closed",
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

    assert invoke.call_count == 4
    assert verdict.exact
    assert verdict.details["dependency_authority_state"] == "authoritative"
    assert verdict.details["dependency_authority_preservable"] is True
    assert verdict.details["dependency_observation_count"] == 2
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_authority_closure"
