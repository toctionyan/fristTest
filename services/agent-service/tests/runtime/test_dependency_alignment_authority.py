from __future__ import annotations

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


def test_adversarial_independence_closure_makes_empty_graph_authoritative() -> None:
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
    assert graph["complete"] is True
    assert graph["edges"] == []
    authority = bridge.alignment_dependency_authority_details(ledger, goals=_goals())
    assert authority["dependency_authority_complete"] is True
    assert authority["dependency_authority_graph_match"] is True


def test_adversarial_positive_closure_can_correct_provisional_false_absence() -> None:
    ledger, _ = bridge.apply_alignment_dependency_proof(
        None,
        user_text="Inspect A, use that result",
        goals=_goals(),
        details=_independent_details(),
        phase="candidate_blind_dependency_reaudit",
    )
    ledger, graph = bridge.apply_alignment_dependency_proof(
        ledger,
        user_text="Inspect A, use that result",
        goals=_goals(),
        details=_positive_details(),
        phase="candidate_blind_dependency_independence_adjudication",
    )
    assert graph["complete"] is True
    assert graph["edges"] == [
        {"dependent_goal_id": "g2", "requires_result_of_goal_id": "g1"}
    ]
    authority = bridge.alignment_dependency_authority_details(ledger, goals=_goals())
    assert authority["dependency_authority_graph_match"] is False
    assert authority["dependency_authority_missing_edges"] == [
        {"dependent_goal_id": "g2", "requires_result_of_goal_id": "g1"}
    ]


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
