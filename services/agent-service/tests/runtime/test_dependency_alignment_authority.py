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


def _validated_obligation_record(
    *,
    contract: str,
    obligation: str,
    result: str,
    relation: str,
    premise_digest: str,
    evidence: dict,
) -> dict:
    digest = proof.canonical_digest({
        "contract": contract,
        "obligation": obligation,
        "pair": ["g1", "g2"],
        "relation": relation,
        "premise_digest": premise_digest,
        "result": result,
        "evidence": evidence,
    })
    return {
        "contract": contract,
        "result": result,
        "evidence": deepcopy(evidence),
        "evidence_digest": digest,
    }


def _with_validated_obligations(
    details: dict,
    *,
    user_text: str,
    goals: list[dict],
    relation: str,
    target_result: str = proof.PASS,
    counterfactual_result: str = proof.PASS,
) -> dict:
    premise_digest = bridge.alignment_dependency_premise_digest(
        user_text=user_text,
        goals=goals,
    )
    enriched = deepcopy(details)
    enriched["dependency_obligation_evidence"] = {
        "contract": bridge.DEPENDENCY_OBLIGATION_EVIDENCE_CONTRACT,
        "pairs": [
            {
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": relation,
                "premise_digest": premise_digest,
                "target_compatibility": _validated_obligation_record(
                    contract=bridge.TARGET_COMPATIBILITY_EVIDENCE_CONTRACT,
                    obligation="target_compatibility",
                    result=target_result,
                    relation=relation,
                    premise_digest=premise_digest,
                    evidence={
                        "validator": "semantic-target-proof-fixture",
                        "finding": "target-compatible"
                        if target_result == proof.PASS
                        else "target-incompatible",
                    },
                ),
                "counterfactual": _validated_obligation_record(
                    contract=bridge.COUNTERFACTUAL_EVIDENCE_CONTRACT,
                    obligation="counterfactual",
                    result=counterfactual_result,
                    relation=relation,
                    premise_digest=premise_digest,
                    evidence={
                        "validator": "result-removal-counterfactual-fixture",
                        "finding": "counterfactual-satisfied"
                        if counterfactual_result == proof.PASS
                        else "counterfactual-failed",
                    },
                ),
            }
        ],
    }
    return enriched


def test_broad_complete_matching_proof_is_provisional_not_authority() -> None:
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


def test_adversarial_independence_closure_without_obligation_evidence_stays_unresolved() -> None:
    ledger, _ = bridge.apply_alignment_dependency_proof(
        None,
        user_text="Inspect A, summarize B",
        goals=_goals(),
        details=_independent_details(),
        phase="candidate_blind_dependency_reaudit",
    )
    ledger, graph = bridge.apply_alignment_dependency_proof(
        ledger,
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
    assert authority["dependency_authority_graph_match"] is False
    assert authority["dependency_authority_unresolved_pairs"] == [["g1", "g2"]]


def test_adversarial_positive_closure_without_obligation_evidence_stays_unresolved() -> None:
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
    authority = bridge.alignment_dependency_authority_details(ledger, goals=goals)
    assert authority["dependency_authority_complete"] is False
    assert authority["dependency_authority_edges"] == []


def test_raw_pair_fields_cannot_self_elevate_obligation_authority() -> None:
    details = _positive_details(match=True)
    details["dependency_pair_decisions"][0].update({
        "target_compatibility": proof.PASS,
        "counterfactual": proof.PASS,
        "counterfactual_proof_digest": "f" * 64,
    })
    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text="Inspect A, use that result",
        goals=_goals(["g1"]),
        details=details,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert graph["complete"] is False
    state = ledger["states"]["g1::g2"]
    assert state["maturity"] == proof.GROUNDED
    assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT
    assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT


def test_explicit_validated_obligation_evidence_can_reach_authority() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    details = _with_validated_obligations(
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
    state = ledger["states"]["g1::g2"]
    assert state["maturity"] == proof.AUTHORITATIVE
    assert state["obligations"]["target_compatibility"] == proof.PASS
    assert state["obligations"]["counterfactual"] == proof.PASS
    authority = bridge.alignment_dependency_authority_details(ledger, goals=goals)
    assert authority["dependency_authority_complete"] is True
    assert authority["dependency_authority_graph_match"] is True


def test_explicit_target_incompatibility_rejects_pair_even_in_closure_phase() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    details = _with_validated_obligations(
        _positive_details(match=True),
        user_text=user_text,
        goals=goals,
        relation="b_depends_on_a",
        target_result=proof.FAIL,
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
    state = ledger["states"]["g1::g2"]
    assert state["maturity"] == proof.REJECTED
    assert state["obligations"]["target_compatibility"] == proof.FAIL
    assert state["obligations"]["counterfactual"] == proof.PASS


def test_explicit_counterfactual_failure_rejects_pair_even_in_closure_phase() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    details = _with_validated_obligations(
        _positive_details(match=True),
        user_text=user_text,
        goals=goals,
        relation="b_depends_on_a",
        counterfactual_result=proof.FAIL,
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
    state = ledger["states"]["g1::g2"]
    assert state["maturity"] == proof.REJECTED
    assert state["obligations"]["target_compatibility"] == proof.PASS
    assert state["obligations"]["counterfactual"] == proof.FAIL


def test_invalid_counterfactual_evidence_digest_fails_closed_to_unknown() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    details = _with_validated_obligations(
        _positive_details(match=True),
        user_text=user_text,
        goals=goals,
        relation="b_depends_on_a",
    )
    pair_evidence = details["dependency_obligation_evidence"]["pairs"][0]
    pair_evidence["counterfactual"]["evidence_digest"] = "0" * 64

    ledger, graph = bridge.apply_alignment_dependency_proof(
        None,
        user_text=user_text,
        goals=goals,
        details=details,
        phase="candidate_blind_dependency_positive_edge_adjudication",
    )
    assert graph["complete"] is False
    assert graph["edges"] == []
    state = ledger["states"]["g1::g2"]
    assert state["maturity"] == proof.GROUNDED
    assert state["obligations"]["target_compatibility"] == proof.PASS
    assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT


def test_validated_evidence_bound_to_another_premise_is_ignored() -> None:
    user_text = "Inspect A, use that result"
    goals = _goals(["g1"])
    details = _with_validated_obligations(
        _positive_details(match=True),
        user_text=user_text,
        goals=goals,
        relation="b_depends_on_a",
    )
    details["dependency_obligation_evidence"]["pairs"][0]["premise_digest"] = "a" * 64

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
    assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT


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
