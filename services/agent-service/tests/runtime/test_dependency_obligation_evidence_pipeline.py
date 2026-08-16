from __future__ import annotations

from copy import deepcopy

from agent_core.goal_graph import dependency_alignment as bridge
from agent_core.goal_graph import dependency_proof as proof
from agent_core.lifecycle import goal_planning as planning


def _goals(depends: list[str] | None = None) -> list[dict]:
    return [
        {
            "goal_id": "g1",
            "evidence_span": "Inspect A",
            "requested_effect": {
                "domain": "open",
                "operation": "inspect",
                "object_type": "record",
            },
            "target_candidate": {"kind": "record", "scope_constraints": []},
            "depends_on": [],
        },
        {
            "goal_id": "g2",
            "evidence_span": "use that result",
            "requested_effect": {
                "domain": "open",
                "operation": "use",
                "object_type": "record",
            },
            "target_candidate": {"kind": "record", "scope_constraints": []},
            "depends_on": list(depends or []),
        },
    ]


def _details(relation: str, *, match: bool = True) -> dict:
    row = {
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": relation,
    }
    if relation != "independent":
        row.update(
            {
                "basis_kind": "result_reference",
                "basis_span": "that result",
            }
        )
    return {
        "dependency_authority": "independent_goal_alignment",
        "dependency_proof_complete": True,
        "dependency_graph_match": match,
        "dependency_pair_decisions": [row],
    }


def _verdict(
    *,
    relation: str,
    verdict: str = "exact",
    reason_code: str = "goal_alignment_candidate_blind_dependency_reaudit_exact",
    missing_spans: tuple[str, ...] = (),
) -> planning.GoalAlignmentVerdict:
    return planning.GoalAlignmentVerdict(
        verdict,
        ("Inspect A", "use that result") if verdict == "exact" else ("Inspect A",),
        missing_spans,
        reason_code,
        "model",
        True,
        _details(relation),
    )


def test_semantic_producer_positive_pair_reaches_unchanged_reducer_authority() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    verdict = _verdict(relation="b_depends_on_a")
    envelope = planning._validated_dependency_obligation_evidence(
        user_text=user_text,
        goals=goals,
        verdict=verdict,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert envelope["contract"] == bridge.DEPENDENCY_OBLIGATION_EVIDENCE_CONTRACT
    assert envelope["producer"] == bridge.DEPENDENCY_OBLIGATION_EVIDENCE_PRODUCER
    assert envelope["premise_digest"] == bridge.alignment_dependency_premise_digest(
        user_text=user_text,
        goals=goals,
    )
    assert envelope["pairs"][0]["target_compatibility"]["result"] == proof.PASS
    assert envelope["pairs"][0]["counterfactual"]["result"] == proof.PASS

    details = {**verdict.details, "dependency_obligation_evidence": envelope}
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text=user_text,
        goals=goals,
        details=details,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert graph["complete"] is True
    assert graph["edges"] == [
        {"dependent_goal_id": "g2", "requires_result_of_goal_id": "g1"}
    ]
    assert ledger["states"]["g1::g2"]["maturity"] == proof.AUTHORITATIVE


def test_semantic_producer_independence_pair_reaches_empty_graph_authority() -> None:
    user_text = "Inspect A, summarize B"
    goals = _goals()
    verdict = _verdict(relation="independent")
    envelope = planning._validated_dependency_obligation_evidence(
        user_text=user_text,
        goals=goals,
        verdict=verdict,
        phase="candidate_blind_dependency_independence_adjudication",
    )
    details = {**verdict.details, "dependency_obligation_evidence": envelope}
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text=user_text,
        goals=goals,
        details=details,
        phase="candidate_blind_dependency_independence_adjudication",
    )
    assert graph["complete"] is True
    assert graph["edges"] == []
    authority = bridge.alignment_dependency_authority_details(ledger, goals=goals)
    assert authority["dependency_authority_complete"] is True
    assert authority["dependency_authority_graph_match"] is True


def test_semantic_incomplete_verdict_does_not_attest_target_compatibility() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    verdict = _verdict(
        relation="b_depends_on_a",
        verdict="incomplete",
        reason_code="semantic_substitution",
        missing_spans=("use that result",),
    )
    envelope = planning._validated_dependency_obligation_evidence(
        user_text=user_text,
        goals=goals,
        verdict=verdict,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert envelope["pairs"][0]["target_compatibility"]["result"] == proof.UNKNOWN_RESULT
    assert envelope["pairs"][0]["counterfactual"]["result"] == proof.PASS

    details = {**verdict.details, "dependency_obligation_evidence": envelope}
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text=user_text,
        goals=goals,
        details=details,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert graph["complete"] is False
    state = ledger["states"]["g1::g2"]
    assert state["maturity"] == proof.GROUNDED
    assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT
    assert state["obligations"]["counterfactual"] == proof.PASS


def test_dependency_only_graph_mismatch_can_attest_semantics_without_adopting_planner_edge() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    verdict = planning.GoalAlignmentVerdict(
        "incomplete",
        ("Inspect A", "use that result"),
        (),
        "goal_alignment_dependency_graph_mismatch",
        "model",
        True,
        _details("independent", match=False),
    )
    envelope = planning._validated_dependency_obligation_evidence(
        user_text=user_text,
        goals=goals,
        verdict=verdict,
        phase="candidate_blind_dependency_independence_adjudication",
    )
    assert envelope["pairs"][0]["target_compatibility"]["result"] == proof.PASS
    assert envelope["pairs"][0]["counterfactual"]["result"] == proof.PASS
    assert envelope["pairs"][0]["relation"] == "independent"


def test_producer_envelope_is_bound_to_frozen_semantic_premise_not_depends_on() -> None:
    user_text = "Inspect A, use that result"
    before_goals = _goals()
    after_goals = _goals(["g1"])
    verdict = _verdict(relation="b_depends_on_a")
    before = planning._validated_dependency_obligation_evidence(
        user_text=user_text,
        goals=before_goals,
        verdict=verdict,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    after = planning._validated_dependency_obligation_evidence(
        user_text=user_text,
        goals=after_goals,
        verdict=verdict,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert before["premise_digest"] == after["premise_digest"]
    assert before["pairs"][0]["premise_digest"] == after["pairs"][0]["premise_digest"]


def test_mutating_semantic_target_after_evidence_creation_invalidates_envelope() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    verdict = _verdict(relation="b_depends_on_a")
    envelope = planning._validated_dependency_obligation_evidence(
        user_text=user_text,
        goals=goals,
        verdict=verdict,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    changed_goals = deepcopy(goals)
    changed_goals[1]["target_candidate"] = {
        "kind": "different-record",
        "scope_constraints": ["other"],
    }
    details = {**verdict.details, "dependency_obligation_evidence": envelope}
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text=user_text,
        goals=changed_goals,
        details=details,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert graph["complete"] is False
    state = ledger["states"]["g1::g2"]
    assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT
    assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT
