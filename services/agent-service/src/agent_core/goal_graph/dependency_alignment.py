from __future__ import annotations

"""Bridge candidate-blind alignment evidence into dependency proof authority.

This module is intentionally language-blind. ``goal_planning`` remains the
semantic verifier boundary and validates literal spans/pair coverage first.
Here we adapt those validated pair decisions into dependency observations and
let the pure dependency-proof reducer decide maturity.

A pair decision is not proof for every dependency obligation. In particular,
target compatibility and current-turn result-removal counterfactual necessity
must arrive as separately validated, premise-bound evidence. Missing, malformed,
or unsupported obligation evidence stays ``UNKNOWN``. An adversarial closure
phase may satisfy only the explicit closure obligation; it cannot manufacture
target/counterfactual proof.
"""

from copy import deepcopy
from typing import Any

from agent_core.goal_graph.dependency_proof import (
    FAIL,
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

ALIGNMENT_DEPENDENCY_PROOF_BRIDGE_VERSION = "alignment-dependency-proof-bridge@2"
DEPENDENCY_OBLIGATION_EVIDENCE_CONTRACT = "validated-dependency-obligation-evidence@1"
TARGET_COMPATIBILITY_EVIDENCE_CONTRACT = "dependency-target-compatibility@1"
COUNTERFACTUAL_EVIDENCE_CONTRACT = "current-turn-result-removal-counterfactual@1"

_ADVERSARIAL_CLOSURE_PHASES = {
    "candidate_blind_dependency_positive_edge_adjudication",
    "candidate_blind_dependency_independence_adjudication",
    "candidate_blind_dependency_effect_collision_adjudication",
    "candidate_blind_dependency_authority_closure",
}
_VALIDATED_OBLIGATION_RESULTS = {PASS, FAIL}
_HEX_DIGITS = frozenset("0123456789abcdef")


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _pair_key(goal_a: str, goal_b: str) -> tuple[str, str]:
    return tuple(sorted((str(goal_a), str(goal_b))))


def _valid_digest(value: Any) -> str | None:
    digest = _text(value, limit=128).casefold()
    if len(digest) != 64 or any(char not in _HEX_DIGITS for char in digest):
        return None
    return digest


def _obligation_evidence_digest(
    *,
    contract: str,
    obligation: str,
    goal_a: str,
    goal_b: str,
    relation: str,
    premise_digest: str,
    result: str,
    evidence: dict[str, Any],
) -> str:
    """Bind one validated obligation record to pair, relation and frozen premise."""

    return canonical_digest({
        "contract": contract,
        "obligation": obligation,
        "pair": list(_pair_key(goal_a, goal_b)),
        "relation": relation,
        "premise_digest": premise_digest,
        "result": result,
        "evidence": deepcopy(evidence),
    })


def _unknown_obligation(reason: str) -> dict[str, Any]:
    return {
        "result": UNKNOWN_RESULT,
        "evidence_digest": None,
        "validated_evidence": None,
        "reason": reason,
    }


def _validated_obligation(
    raw: Any,
    *,
    expected_contract: str,
    obligation: str,
    goal_a: str,
    goal_b: str,
    relation: str,
    premise_digest: str,
) -> dict[str, Any]:
    """Validate an already-adjudicated obligation envelope fail-closed.

    This validates evidence binding/integrity only. It does not interpret user
    language and must never be fed arbitrary raw model fields. The top-level
    alignment validator is responsible for inserting this separate evidence
    contract after its own semantic/counterfactual validation.
    """

    if not isinstance(raw, dict):
        return _unknown_obligation("missing_obligation_evidence")
    if _text(raw.get("contract"), limit=160) != expected_contract:
        return _unknown_obligation("unsupported_obligation_contract")
    result = _text(raw.get("result"), limit=32).upper()
    if result not in _VALIDATED_OBLIGATION_RESULTS:
        return _unknown_obligation("unsupported_obligation_result")
    evidence = raw.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        return _unknown_obligation("missing_obligation_evidence_payload")
    supplied_digest = _valid_digest(raw.get("evidence_digest"))
    if supplied_digest is None:
        return _unknown_obligation("invalid_obligation_evidence_digest")
    expected_digest = _obligation_evidence_digest(
        contract=expected_contract,
        obligation=obligation,
        goal_a=goal_a,
        goal_b=goal_b,
        relation=relation,
        premise_digest=premise_digest,
        result=result,
        evidence=evidence,
    )
    if supplied_digest != expected_digest:
        return _unknown_obligation("obligation_evidence_digest_mismatch")
    return {
        "result": result,
        "evidence_digest": supplied_digest,
        "validated_evidence": {
            "contract": expected_contract,
            "result": result,
            "evidence_digest": supplied_digest,
            "evidence": deepcopy(evidence),
        },
        "reason": "validated_obligation_evidence",
    }


def _validated_pair_obligations(
    details: dict[str, Any],
    *,
    goal_a: str,
    goal_b: str,
    relation: str,
    premise_digest: str,
) -> dict[str, dict[str, Any]]:
    """Consume only one exact, premise-bound validated evidence row per pair."""

    unknown = {
        "target_compatibility": _unknown_obligation("missing_validated_evidence_envelope"),
        "counterfactual": _unknown_obligation("missing_validated_evidence_envelope"),
    }
    envelope = details.get("dependency_obligation_evidence")
    if not isinstance(envelope, dict):
        return unknown
    if _text(envelope.get("contract"), limit=160) != DEPENDENCY_OBLIGATION_EVIDENCE_CONTRACT:
        return {
            "target_compatibility": _unknown_obligation("unsupported_validated_evidence_envelope"),
            "counterfactual": _unknown_obligation("unsupported_validated_evidence_envelope"),
        }
    rows = envelope.get("pairs")
    if not isinstance(rows, list):
        return {
            "target_compatibility": _unknown_obligation("validated_evidence_pairs_required"),
            "counterfactual": _unknown_obligation("validated_evidence_pairs_required"),
        }

    wanted = _pair_key(goal_a, goal_b)
    matches: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row_a = _text(raw.get("goal_a_id"), limit=200)
        row_b = _text(raw.get("goal_b_id"), limit=200)
        if row_a and row_b and _pair_key(row_a, row_b) == wanted:
            matches.append(raw)
    if len(matches) != 1:
        reason = "duplicate_validated_evidence_pair" if len(matches) > 1 else "missing_validated_evidence_pair"
        return {
            "target_compatibility": _unknown_obligation(reason),
            "counterfactual": _unknown_obligation(reason),
        }

    row = matches[0]
    if _text(row.get("relation"), limit=80).casefold() != relation:
        return {
            "target_compatibility": _unknown_obligation("validated_evidence_relation_mismatch"),
            "counterfactual": _unknown_obligation("validated_evidence_relation_mismatch"),
        }
    if _text(row.get("premise_digest"), limit=128).casefold() != premise_digest.casefold():
        return {
            "target_compatibility": _unknown_obligation("validated_evidence_premise_mismatch"),
            "counterfactual": _unknown_obligation("validated_evidence_premise_mismatch"),
        }

    return {
        "target_compatibility": _validated_obligation(
            row.get("target_compatibility"),
            expected_contract=TARGET_COMPATIBILITY_EVIDENCE_CONTRACT,
            obligation="target_compatibility",
            goal_a=goal_a,
            goal_b=goal_b,
            relation=relation,
            premise_digest=premise_digest,
        ),
        "counterfactual": _validated_obligation(
            row.get("counterfactual"),
            expected_contract=COUNTERFACTUAL_EVIDENCE_CONTRACT,
            obligation="counterfactual",
            goal_a=goal_a,
            goal_b=goal_b,
            relation=relation,
            premise_digest=premise_digest,
        ),
    }


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

    ``dependency_pair_decisions`` prove pair coverage/relation observations only.
    Broad complete/matching diagnostics never satisfy proof obligations. An
    adversarial closure phase may satisfy ``adversarial_closure`` only. Target
    compatibility and counterfactual remain UNKNOWN until an exact validated
    obligation evidence envelope is present and bound to this frozen premise.
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

        obligation_evidence = _validated_pair_obligations(
            details,
            goal_a=goal_a,
            goal_b=goal_b,
            relation=relation,
            premise_digest=premise_digest,
        )
        target_evidence = obligation_evidence["target_compatibility"]
        counterfactual_evidence = obligation_evidence["counterfactual"]

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
            "obligation_evidence_contract": DEPENDENCY_OBLIGATION_EVIDENCE_CONTRACT,
            "target_compatibility_evidence": deepcopy(target_evidence.get("validated_evidence")),
            "target_compatibility_evidence_status": target_evidence.get("reason"),
            "counterfactual_evidence": deepcopy(counterfactual_evidence.get("validated_evidence")),
            "counterfactual_evidence_status": counterfactual_evidence.get("reason"),
        }
        counterfactual_digest = counterfactual_evidence.get("evidence_digest")
        if not counterfactual_digest:
            counterfactual_digest = canonical_digest({
                "contract": COUNTERFACTUAL_EVIDENCE_CONTRACT,
                "obligation": "counterfactual",
                "pair": list(_pair_key(goal_a, goal_b)),
                "relation": relation,
                "premise_digest": premise_digest,
                "result": UNKNOWN_RESULT,
                "reason": counterfactual_evidence.get("reason"),
            })

        observation = make_dependency_observation(
            goal_a_id=goal_a,
            goal_b_id=goal_b,
            relation=relation,
            premise_digest=premise_digest,
            evidence_payload=decision_evidence,
            obligations={
                "grounding": PASS,
                "semantic_compatibility": PASS,
                "target_compatibility": target_evidence["result"],
                "counterfactual": counterfactual_evidence["result"],
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
            counterfactual_proof_digest=str(counterfactual_digest),
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
    "COUNTERFACTUAL_EVIDENCE_CONTRACT",
    "DEPENDENCY_OBLIGATION_EVIDENCE_CONTRACT",
    "TARGET_COMPATIBILITY_EVIDENCE_CONTRACT",
    "alignment_dependency_authority_details",
    "alignment_dependency_premise_digest",
    "apply_alignment_dependency_proof",
    "dependency_authority_closed_and_matching",
]
