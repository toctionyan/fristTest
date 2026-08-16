from __future__ import annotations

"""Bridge candidate-blind alignment evidence into dependency proof authority.

This module is intentionally language-blind. ``goal_planning`` remains the
semantic verifier boundary and validates literal spans/pair coverage first.
Here we seal those validated pair decisions as observations and let the pure
dependency-proof reducer decide maturity. Broad verifier passes are provisional;
only an explicit adversarial graph-closure phase can satisfy the final closure
obligation.
"""

from copy import deepcopy
from typing import Any

from agent_core.goal_graph.dependency_proof import (
    PASS,
    UNKNOWN_RESULT,
    apply_dependency_observation,
    canonical_digest,
    dependency_authority_for_pair,
    dependency_graph_diff,
    dependency_graph_from_ledger,
    make_dependency_observation,
    make_dependency_proof_ledger,
)

ALIGNMENT_DEPENDENCY_PROOF_BRIDGE_VERSION = "alignment-dependency-proof-bridge@1"

_ADVERSARIAL_CLOSURE_PHASES = {
    "candidate_blind_dependency_positive_edge_adjudication",
    "candidate_blind_dependency_independence_adjudication",
    "candidate_blind_dependency_effect_collision_adjudication",
    "candidate_blind_dependency_authority_closure",
}


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def alignment_dependency_premise_digest(
    *,
    user_text: str,
    goals: list[dict[str, Any]],
) -> str:
    """Digest frozen semantic premises while deliberately excluding depends_on."""

    projected: list[dict[str, Any]] = []
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        projected.append({
            "goal_id": _text(goal.get("goal_id"), limit=200),
            "evidence_span": _text(goal.get("evidence_span"), limit=500),
            "requested_effect": deepcopy(goal.get("requested_effect"))
            if isinstance(goal.get("requested_effect"), dict)
            else None,
            "target_candidate": deepcopy(goal.get("target_candidate"))
            if isinstance(goal.get("target_candidate"), dict)
            else None,
            "reference_expression": deepcopy(goal.get("reference_expression"))
            if isinstance(goal.get("reference_expression"), dict)
            else None,
            "condition": deepcopy(goal.get("condition"))
            if isinstance(goal.get("condition"), dict)
            else None,
            "expected_result_cardinality": _text(
                goal.get("expected_result_cardinality"), limit=80
            ),
            "required": bool(goal.get("required", True)),
        })
    return canonical_digest({
        "version": ALIGNMENT_DEPENDENCY_PROOF_BRIDGE_VERSION,
        "user_text": str(user_text or ""),
        "goals": projected,
    })


def apply_alignment_dependency_proof(
    ledger: dict[str, Any] | None,
    *,
    user_text: str,
    goals: list[dict[str, Any]],
    details: dict[str, Any],
    phase: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one already-validated pairwise graph observation set.

    A complete/matching broad pass is deliberately not enough: it leaves
    ``adversarial_closure=UNKNOWN``. A later graph-closure phase can mature each
    pair to authority without relying on call number.
    """

    current = deepcopy(ledger) if isinstance(ledger, dict) else make_dependency_proof_ledger()
    premise_digest = alignment_dependency_premise_digest(user_text=user_text, goals=goals)
    rows = details.get("dependency_pair_decisions")
    if details.get("dependency_proof_complete") is not True or not isinstance(rows, list):
        return current, dependency_graph_from_ledger(
            current,
            goal_ids=[str(goal.get("goal_id") or "") for goal in goals if isinstance(goal, dict)],
        )

    closure_result = PASS if str(phase or "") in _ADVERSARIAL_CLOSURE_PHASES else UNKNOWN_RESULT
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        goal_a = _text(raw.get("goal_a_id"), limit=200)
        goal_b = _text(raw.get("goal_b_id"), limit=200)
        relation = _text(raw.get("relation"), limit=80).casefold()
        if not goal_a or not goal_b or not relation:
            continue

        prior = dependency_authority_for_pair(current, goal_a, goal_b)
        supersedes = None
        if (
            prior is not None
            and str(prior.get("authority_relation") or prior.get("relation") or "") != relation
        ):
            supersedes = _text(prior.get("authority_evidence_digest"), limit=128) or None

        decision_evidence = {
            "bridge_version": ALIGNMENT_DEPENDENCY_PROOF_BRIDGE_VERSION,
            "phase": str(phase or ""),
            "decision": deepcopy(raw),
            "pairwise_proof_complete": True,
            "source_authority": details.get("dependency_authority"),
        }
        observation = make_dependency_observation(
            goal_a_id=goal_a,
            goal_b_id=goal_b,
            relation=relation,
            premise_digest=premise_digest,
            evidence_payload=decision_evidence,
            obligations={
                "grounding": PASS,
                "semantic_compatibility": PASS,
                "target_compatibility": PASS,
                "counterfactual": PASS,
                "structural_validity": PASS,
                "contradiction_free": PASS,
                "adversarial_closure": closure_result,
                "pair_coverage": PASS,
            },
            grounding_proof_digest=canonical_digest({
                "pair": sorted((goal_a, goal_b)),
                "decision": raw,
                "proof_complete": True,
            }),
            counterfactual_proof_digest=canonical_digest({
                "contract": "current-turn-result-removal-counterfactual@1",
                "decision": raw,
                "phase": str(phase or ""),
            }),
            source=f"goal_alignment:{phase or 'unspecified'}",
            basis_kind=_text(raw.get("basis_kind"), limit=80) or None,
            basis_span=_text(raw.get("basis_span"), limit=240) or None,
            supersedes_evidence_digest=supersedes,
            diagnostic_complete=bool(details.get("dependency_proof_complete")),
            diagnostic_matching=bool(details.get("dependency_graph_match")),
        )
        current = apply_dependency_observation(
            current,
            observation,
            current_premise_digest=premise_digest,
        )

    graph = dependency_graph_from_ledger(
        current,
        goal_ids=[str(goal.get("goal_id") or "") for goal in goals if isinstance(goal, dict)],
    )
    return current, graph


def alignment_dependency_authority_details(
    ledger: dict[str, Any] | None,
    *,
    goals: list[dict[str, Any]],
) -> dict[str, Any]:
    current = deepcopy(ledger) if isinstance(ledger, dict) else make_dependency_proof_ledger()
    declared_edges = [
        {
            "dependent_goal_id": str(goal.get("goal_id") or ""),
            "requires_result_of_goal_id": str(prerequisite),
        }
        for goal in goals
        if isinstance(goal, dict)
        for prerequisite in list(goal.get("depends_on") or [])
        if str(goal.get("goal_id") or "") and str(prerequisite)
    ]
    diff = dependency_graph_diff(
        current,
        goal_ids=[str(goal.get("goal_id") or "") for goal in goals if isinstance(goal, dict)],
        declared_edges=declared_edges,
    )
    return {
        "dependency_maturity_authority": "deterministic_dependency_proof_reducer",
        "dependency_authority_complete": bool(diff.get("repairable")),
        "dependency_authority_graph_match": diff.get("reason_code") == "DEPENDENCY_GRAPH_MATCH",
        "dependency_authority_edges": list(diff.get("authoritative_edges") or []),
        "dependency_authority_missing_edges": list(diff.get("missing_edges") or []),
        "dependency_authority_extra_edges": list(diff.get("extra_edges") or []),
        "dependency_authority_unresolved_pairs": list(diff.get("unresolved_pairs") or []),
        "dependency_authority_graph_proof_digest": diff.get("graph_proof_digest"),
        "dependency_authority_ledger_digest": current.get("ledger_digest"),
    }


def dependency_authority_closed_and_matching(details: dict[str, Any]) -> bool:
    return bool(
        isinstance(details, dict)
        and details.get("dependency_authority_complete") is True
        and details.get("dependency_authority_graph_match") is True
    )


__all__ = [
    "ALIGNMENT_DEPENDENCY_PROOF_BRIDGE_VERSION",
    "alignment_dependency_authority_details",
    "alignment_dependency_premise_digest",
    "apply_alignment_dependency_proof",
    "dependency_authority_closed_and_matching",
]
