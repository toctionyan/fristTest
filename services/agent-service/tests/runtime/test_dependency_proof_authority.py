from __future__ import annotations

from copy import deepcopy

from agent_core.goal_graph import dependency_proof as proof

PREMISE = "semantic-premise-v1"


def _obligations(**overrides: str) -> dict[str, str]:
    base = {
        "grounding": "PASS",
        "semantic_compatibility": "PASS",
        "target_compatibility": "PASS",
        "counterfactual": "PASS",
        "structural_validity": "PASS",
        "contradiction_free": "PASS",
        "pair_coverage": "PASS",
    }
    base.update(overrides)
    return base


def _observation(
    relation: str,
    *,
    evidence: str = "e1",
    obligations_override: dict[str, str] | None = None,
    supersedes: str | None = None,
    complete: bool = True,
    matching: bool = True,
) -> dict:
    return proof.make_dependency_observation(
        goal_a_id="g1",
        goal_b_id="g2",
        relation=relation,
        premise_digest=PREMISE,
        evidence_payload={"evidence": evidence},
        obligations=_obligations(**(obligations_override or {})),
        grounding_proof_digest=f"ground:{evidence}",
        counterfactual_proof_digest=f"cf:{evidence}",
        source="test",
        basis_kind=("result_reference" if relation != "independent" else None),
        basis_span=("that result" if relation != "independent" else None),
        supersedes_evidence_digest=supersedes,
        diagnostic_complete=complete,
        diagnostic_matching=matching,
    )


def test_complete_and_matching_do_not_mint_absence_authority_when_counterfactual_fails() -> None:
    ledger = proof.apply_dependency_observation(
        None,
        _observation("independent", obligations_override={"counterfactual": "FAIL"}),
        current_premise_digest=PREMISE,
    )
    state = ledger["states"]["g1::g2"]
    assert state["maturity"] == proof.REJECTED
    assert state["diagnostic_claims"]["complete"] is True
    assert state["diagnostic_claims"]["matching"] is True
    assert proof.dependency_authority_for_pair(ledger, "g1", "g2") is None


def test_present_dependency_becomes_authority_only_when_all_obligations_pass() -> None:
    ledger = proof.apply_dependency_observation(
        None,
        _observation("b_depends_on_a"),
        current_premise_digest=PREMISE,
    )
    authority = proof.dependency_authority_for_pair(ledger, "g1", "g2")
    assert authority is not None
    assert authority["authority_relation"] == "b_depends_on_a"
    assert authority["maturity"] == proof.AUTHORITATIVE


def test_repeated_unverified_opinions_cannot_downgrade_authority() -> None:
    ledger = proof.apply_dependency_observation(
        None,
        _observation("b_depends_on_a", evidence="base"),
        current_premise_digest=PREMISE,
    )
    original = proof.dependency_authority_for_pair(ledger, "g1", "g2")
    assert original is not None
    for index in range(20):
        ledger = proof.apply_dependency_observation(
            ledger,
            _observation(
                "independent",
                evidence=f"weak-{index}",
                obligations_override={"counterfactual": "UNKNOWN"},
            ),
            current_premise_digest=PREMISE,
        )
        authority = proof.dependency_authority_for_pair(ledger, "g1", "g2")
        assert authority is not None
        assert authority["authority_digest"] == original["authority_digest"]
        assert authority["authority_relation"] == "b_depends_on_a"


def test_opposite_even_verified_vote_without_binding_cannot_downgrade_authority() -> None:
    ledger = proof.apply_dependency_observation(
        None,
        _observation("b_depends_on_a", evidence="base"),
        current_premise_digest=PREMISE,
    )
    original = proof.dependency_authority_for_pair(ledger, "g1", "g2")
    assert original is not None
    ledger = proof.apply_dependency_observation(
        ledger,
        _observation("independent", evidence="different-opinion"),
        current_premise_digest=PREMISE,
    )
    authority = proof.dependency_authority_for_pair(ledger, "g1", "g2")
    assert authority is not None
    assert authority["authority_digest"] == original["authority_digest"]
    assert authority["reason_code"] == "COUNTEREVIDENCE_NOT_BOUND_TO_CURRENT_AUTHORITY"


def test_new_grounded_counterevidence_challenges_then_distinct_reclosure_can_flip_authority() -> None:
    ledger = proof.apply_dependency_observation(
        None,
        _observation("b_depends_on_a", evidence="base"),
        current_premise_digest=PREMISE,
    )
    original = proof.dependency_authority_for_pair(ledger, "g1", "g2")
    assert original is not None
    ledger = proof.apply_dependency_observation(
        ledger,
        _observation(
            "independent",
            evidence="counter-1",
            supersedes=original["authority_evidence_digest"],
        ),
        current_premise_digest=PREMISE,
    )
    assert ledger["states"]["g1::g2"]["maturity"] == proof.CHALLENGED
    assert proof.dependency_authority_for_pair(ledger, "g1", "g2") is None

    ledger = proof.apply_dependency_observation(
        ledger,
        _observation("independent", evidence="counter-1"),
        current_premise_digest=PREMISE,
    )
    assert ledger["states"]["g1::g2"]["maturity"] == proof.CHALLENGED

    ledger = proof.apply_dependency_observation(
        ledger,
        _observation("independent", evidence="counter-2"),
        current_premise_digest=PREMISE,
    )
    authority = proof.dependency_authority_for_pair(ledger, "g1", "g2")
    assert authority is not None
    assert authority["authority_relation"] == "independent"


def test_empty_dependency_graph_is_complete_only_with_authoritative_absence_for_every_pair() -> None:
    ledger = proof.make_dependency_proof_ledger()
    graph = proof.dependency_graph_from_ledger(ledger, goal_ids=["g1", "g2"])
    assert graph["complete"] is False
    assert graph["edges"] == []
    assert graph["unresolved_pairs"] == [["g1", "g2"]]

    ledger = proof.apply_dependency_observation(
        ledger,
        _observation("independent", evidence="absence-proof"),
        current_premise_digest=PREMISE,
    )
    graph = proof.dependency_graph_from_ledger(ledger, goal_ids=["g1", "g2"])
    assert graph["complete"] is True
    assert graph["edges"] == []
    assert graph["independent_pairs"] == [["g1", "g2"]]


def test_absence_without_pair_coverage_cannot_become_authority() -> None:
    ledger = proof.apply_dependency_observation(
        None,
        _observation("independent", obligations_override={"pair_coverage": "UNKNOWN"}),
        current_premise_digest=PREMISE,
    )
    assert ledger["states"]["g1::g2"]["maturity"] == proof.GROUNDED
    assert proof.dependency_authority_for_pair(ledger, "g1", "g2") is None


def test_grounding_pass_cannot_short_circuit_failed_counterfactual() -> None:
    ledger = proof.apply_dependency_observation(
        None,
        _observation(
            "b_depends_on_a",
            obligations_override={"grounding": "PASS", "counterfactual": "FAIL"},
        ),
        current_premise_digest=PREMISE,
    )
    assert ledger["states"]["g1::g2"]["maturity"] == proof.REJECTED


def test_cross_target_incompatibility_blocks_positive_dependency_authority() -> None:
    ledger = proof.apply_dependency_observation(
        None,
        _observation("b_depends_on_a", obligations_override={"target_compatibility": "FAIL"}),
        current_premise_digest=PREMISE,
    )
    assert ledger["states"]["g1::g2"]["maturity"] == proof.REJECTED
    assert proof.dependency_authority_for_pair(ledger, "g1", "g2") is None


def test_premise_change_marks_old_authority_stale() -> None:
    ledger = proof.apply_dependency_observation(
        None,
        _observation("b_depends_on_a", evidence="base"),
        current_premise_digest=PREMISE,
    )
    ledger = proof.apply_dependency_observation(
        ledger,
        _observation("b_depends_on_a", evidence="same-claim"),
        current_premise_digest="semantic-premise-v2",
    )
    state = ledger["states"]["g1::g2"]
    assert state["maturity"] == proof.STALE
    assert proof.dependency_authority_for_pair(ledger, "g1", "g2") is None


def test_inadmissible_observation_cannot_erase_existing_authority() -> None:
    ledger = proof.apply_dependency_observation(
        None,
        _observation("b_depends_on_a", evidence="base"),
        current_premise_digest=PREMISE,
    )
    original = proof.dependency_authority_for_pair(ledger, "g1", "g2")
    assert original is not None
    bad = _observation("independent", evidence="bad")
    bad["grounding_proof_digest"] = ""
    ledger = proof.apply_dependency_observation(ledger, bad, current_premise_digest=PREMISE)
    authority = proof.dependency_authority_for_pair(ledger, "g1", "g2")
    assert authority is not None
    assert authority["authority_digest"] == original["authority_digest"]
    assert authority["reason_code"] == "INADMISSIBLE_OBSERVATION_CANNOT_DOWNGRADE_AUTHORITY"


def test_graph_diff_uses_authority_not_raw_verifier_matching_flag() -> None:
    ledger = proof.apply_dependency_observation(
        None,
        _observation("b_depends_on_a", evidence="edge"),
        current_premise_digest=PREMISE,
    )
    diff = proof.dependency_graph_diff(
        ledger,
        goal_ids=["g1", "g2"],
        declared_edges=[],
    )
    assert diff["repairable"] is True
    assert diff["reason_code"] == "DEPENDENCY_GRAPH_MISMATCH"
    assert diff["missing_edges"] == [
        {"dependent_goal_id": "g2", "requires_result_of_goal_id": "g1"}
    ]


def test_ledger_and_observation_are_tamper_evident() -> None:
    observation = _observation("b_depends_on_a")
    assert proof.dependency_observation_integrity(observation)["ok"] is True
    tampered = deepcopy(observation)
    tampered["relation"] = "independent"
    assert proof.dependency_observation_integrity(tampered)["ok"] is False

    ledger = proof.apply_dependency_observation(None, observation, current_premise_digest=PREMISE)
    assert proof.dependency_proof_ledger_integrity(ledger)["ok"] is True
    ledger["states"]["g1::g2"]["authority_relation"] = "independent"
    assert proof.dependency_proof_ledger_integrity(ledger)["ok"] is False
