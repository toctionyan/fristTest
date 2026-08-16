from __future__ import annotations

from copy import deepcopy

from agent_core.goal_graph import dependency_alignment as bridge
from agent_core.goal_graph import dependency_proof as proof


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
            "depends_on": list(depends or []),
        },
    ]


def _independent_details(*, match: bool = True) -> dict:
    return {
        "dependency_authority": "independent_goal_alignment",
        "dependency_proof_complete": True,
        "dependency_graph_match": match,
        "dependency_pair_decisions": [
            {"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}
        ],
    }


def _positive_details(*, match: bool = False) -> dict:
    return {
        "dependency_authority": "independent_goal_alignment",
        "dependency_proof_complete": True,
        "dependency_graph_match": match,
        "dependency_pair_decisions": [
            {
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "b_depends_on_a",
                "basis_kind": "result_reference",
                "basis_span": "that result",
            }
        ],
    }


def _with_obligation_evidence(
    details: dict,
    *,
    user_text: str,
    goals: list[dict],
    relation: str,
    target_result: str = proof.PASS,
    counterfactual_result: str = proof.PASS,
) -> dict:
    premise = bridge.alignment_dependency_premise_digest(user_text=user_text, goals=goals)
    enriched = deepcopy(details)
    enriched["dependency_obligation_evidence"] = {
        "contract": bridge.DEPENDENCY_OBLIGATION_EVIDENCE_CONTRACT,
        "producer": bridge.DEPENDENCY_OBLIGATION_EVIDENCE_PRODUCER,
        "premise_digest": premise,
        "pairs": [
            {
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": relation,
                "premise_digest": premise,
                "target_compatibility": {
                    "contract": bridge.TARGET_COMPATIBILITY_EVIDENCE_CONTRACT,
                    "result": target_result,
                    "evidence": {"source": "validated-target-fixture"},
                },
                "counterfactual": {
                    "contract": bridge.COUNTERFACTUAL_EVIDENCE_CONTRACT,
                    "result": counterfactual_result,
                    "evidence": {"source": "validated-counterfactual-fixture"},
                },
            }
        ],
    }
    return enriched


def test_broad_complete_matching_pair_decision_is_observation_only() -> None:
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text="Inspect A, use that result",
        goals=_goals(),
        details=_independent_details(),
        phase="candidate_blind_dependency_reaudit",
    )
    assert graph["complete"] is False
    state = ledger["states"]["g1::g2"]
    assert state["maturity"] == proof.GROUNDED
    assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT
    assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT


def test_adversarial_independence_phase_without_obligation_evidence_stays_unresolved() -> None:
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text="Inspect A, summarize B",
        goals=_goals(),
        details=_independent_details(),
        phase="candidate_blind_dependency_independence_adjudication",
    )
    assert graph["complete"] is False
    assert graph["edges"] == []
    state = ledger["states"]["g1::g2"]
    assert state["maturity"] == proof.GROUNDED
    assert state["obligations"]["adversarial_closure"] == proof.PASS
    assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT
    assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT
    authority = bridge.alignment_dependency_authority_details(ledger, goals=_goals())
    assert authority["dependency_authority_complete"] is False
    assert authority["dependency_authority_unresolved_pairs"] == [["g1", "g2"]]


def test_adversarial_positive_phase_without_obligation_evidence_stays_unresolved() -> None:
    goals = _goals(["g1"])
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text="Inspect A, use that result",
        goals=goals,
        details=_positive_details(match=True),
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert graph["complete"] is False
    assert graph["edges"] == []
    state = ledger["states"]["g1::g2"]
    assert state["maturity"] == proof.GROUNDED
    assert state["obligations"]["adversarial_closure"] == proof.PASS
    assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT
    assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT


def test_raw_pair_obligation_fields_cannot_self_elevate_authority() -> None:
    details = _positive_details(match=True)
    details["dependency_pair_decisions"][0].update(
        {
            "target_compatibility": proof.PASS,
            "counterfactual": proof.PASS,
            "counterfactual_proof_digest": "f" * 64,
            "dependency_obligation_evidence": {
                "result": proof.PASS,
            },
        }
    )
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text="Inspect A, use that result",
        goals=_goals(["g1"]),
        details=details,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert graph["complete"] is False
    state = ledger["states"]["g1::g2"]
    assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT
    assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT


def test_wrong_producer_or_premise_fails_closed() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    details = _with_obligation_evidence(
        _positive_details(match=True),
        user_text=user_text,
        goals=goals,
        relation="b_depends_on_a",
    )
    details["dependency_obligation_evidence"]["producer"] = "untrusted-model-field"
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text=user_text,
        goals=goals,
        details=details,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert graph["complete"] is False
    state = ledger["states"]["g1::g2"]
    assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT
    assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT

    details = _with_obligation_evidence(
        _positive_details(match=True),
        user_text=user_text,
        goals=goals,
        relation="b_depends_on_a",
    )
    details["dependency_obligation_evidence"]["premise_digest"] = "0" * 64
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text=user_text,
        goals=goals,
        details=details,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert graph["complete"] is False
    state = ledger["states"]["g1::g2"]
    assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT
    assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT


def test_relation_mismatch_or_duplicate_evidence_fails_closed() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    details = _with_obligation_evidence(
        _positive_details(match=True),
        user_text=user_text,
        goals=goals,
        relation="independent",
    )
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text=user_text,
        goals=goals,
        details=details,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert graph["complete"] is False

    details = _with_obligation_evidence(
        _positive_details(match=True),
        user_text=user_text,
        goals=goals,
        relation="b_depends_on_a",
    )
    details["dependency_obligation_evidence"]["pairs"].append(
        deepcopy(details["dependency_obligation_evidence"]["pairs"][0])
    )
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text=user_text,
        goals=goals,
        details=details,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert graph["complete"] is False
    state = ledger["states"]["g1::g2"]
    assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT
    assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT


def test_validated_positive_obligation_evidence_can_reach_authority() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    details = _with_obligation_evidence(
        _positive_details(match=True),
        user_text=user_text,
        goals=goals,
        relation="b_depends_on_a",
    )
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


def test_validated_independence_obligation_evidence_can_reach_empty_graph_authority() -> None:
    user_text = "Inspect A, summarize B"
    goals = _goals()
    details = _with_obligation_evidence(
        _independent_details(match=True),
        user_text=user_text,
        goals=goals,
        relation="independent",
    )
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


def test_explicit_target_or_counterfactual_failure_rejects_pair() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    for target_result, counterfactual_result in (
        (proof.FAIL, proof.PASS),
        (proof.PASS, proof.FAIL),
    ):
        details = _with_obligation_evidence(
            _positive_details(match=True),
            user_text=user_text,
            goals=goals,
            relation="b_depends_on_a",
            target_result=target_result,
            counterfactual_result=counterfactual_result,
        )
        ledger, graph = bridge.apply_alignment_dependency_proof(
            None,
            user_text=user_text,
            goals=goals,
            details=details,
            phase="candidate_blind_dependency_positive_edge_adjudication",
        )
        assert graph["complete"] is False
        assert graph["edges"] == []
        assert ledger["states"]["g1::g2"]["maturity"] == proof.REJECTED


def test_dependency_repair_does_not_invalidate_semantic_premise_digest() -> None:
    before = bridge.alignment_dependency_premise_digest(
        user_text="Inspect A, use that result",
        goals=_goals(),
    )
    after = bridge.alignment_dependency_premise_digest(
        user_text="Inspect A, use that result",
        goals=_goals(["g1"]),
    )
    assert before == after
