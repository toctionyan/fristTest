from __future__ import annotations

"""Pure maturity reducer for semantic dependency proof observations.

The model verifier produces observations; this module decides only proof
maturity. It deliberately owns no request-local/global session and never wraps
or mutates the Goal-alignment parser helpers. Request orchestration keeps the
current proof in local verifier state and feeds observations into these pure
functions.
"""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any


DEPENDENCY_PROOF_PROTOCOL_VERSION = "semantic-dependency-proof-authority@3"


class DependencyProofMaturity(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    AUTHORITATIVE = "authoritative"
    STALE = "stale"
    REJECTED = "rejected"


@dataclass(frozen=True)
class DependencyGraphObservation:
    premise_digest: str
    edges: tuple[tuple[str, str], ...]
    complete: bool
    graph_matches_declaration: bool
    expected_pair_count: int
    observed_pair_count: int
    source: str

    @property
    def structurally_admissible(self) -> bool:
        # A graph may disagree with Planner and still be valid proof evidence;
        # graph_matches_declaration therefore is metadata, not admissibility.
        return bool(
            self.premise_digest
            and self.complete
            and self.expected_pair_count == self.observed_pair_count
        )


@dataclass(frozen=True)
class DependencyGraphProof:
    premise_digest: str
    edges: tuple[tuple[str, str], ...]
    maturity: DependencyProofMaturity
    observation_count: int
    preservable: bool
    dependency_challenge_required: bool
    source: str
    reason_code: str

    @property
    def authoritative(self) -> bool:
        return self.maturity == DependencyProofMaturity.AUTHORITATIVE


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, set):
        return [_canonical(item) for item in sorted(value, key=str)]
    return value


def dependency_premise_digest(*, user_text: str, goals: list[dict[str, Any]]) -> str:
    """Seal semantic premises without making Planner depends_on its own authority."""

    projected_goals: list[dict[str, Any]] = []
    for raw in goals:
        if not isinstance(raw, dict):
            continue
        row = {
            key: deepcopy(raw.get(key))
            for key in (
                "goal_id",
                "evidence_span",
                "requested_effect",
                "target_candidate",
                "reference_expression",
                "condition",
                "expected_result_cardinality",
                "required",
            )
            if raw.get(key) is not None
        }
        projected_goals.append(row)
    projected_goals.sort(key=lambda row: str(row.get("goal_id") or ""))
    encoded = json.dumps(
        _canonical({
            "user_text": str(user_text or ""),
            "goals": projected_goals,
        }),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def reduce_dependency_graph_proof(
    previous: DependencyGraphProof | None,
    observation: DependencyGraphObservation,
) -> DependencyGraphProof:
    """Reduce one observation into deterministic dependency proof maturity.

    Multi-Goal policy:
    - first complete candidate-blind pairwise graph => VERIFIED/preservable;
    - second admissible graph under the same frozen premise => AUTHORITATIVE;
    - once AUTHORITATIVE, later same-premise observations can only preserve it;
    - changed premises must start a fresh proof sequence.

    This is not a vote counter. The verifier orchestration only feeds the second
    pairwise observation from its bounded dependency-specific adversarial slot.
    Therefore the VERIFIED->AUTHORITATIVE transition denotes challenge closure,
    while later model opinions cannot replace already closed authority.
    """

    same_premise = bool(
        previous is not None
        and previous.premise_digest == observation.premise_digest
    )
    previous_count = previous.observation_count if previous is not None else 0

    if same_premise and previous is not None and previous.authoritative:
        return DependencyGraphProof(
            premise_digest=previous.premise_digest,
            edges=previous.edges,
            maturity=DependencyProofMaturity.AUTHORITATIVE,
            observation_count=previous.observation_count + 1,
            preservable=True,
            dependency_challenge_required=False,
            source=observation.source,
            reason_code=(
                "authoritative_graph_preserved_under_repeat_observation"
                if observation.structurally_admissible and previous.edges == observation.edges
                else "authoritative_graph_revote_rejected"
            ),
        )

    if not observation.structurally_admissible:
        if same_premise and previous is not None and previous.preservable:
            return DependencyGraphProof(
                premise_digest=previous.premise_digest,
                edges=previous.edges,
                maturity=previous.maturity,
                observation_count=previous.observation_count + 1,
                preservable=True,
                dependency_challenge_required=previous.dependency_challenge_required,
                source=observation.source,
                reason_code="inadmissible_observation_preserved_previous_proof",
            )
        return DependencyGraphProof(
            premise_digest=observation.premise_digest,
            edges=observation.edges,
            maturity=DependencyProofMaturity.REJECTED,
            observation_count=previous_count + 1,
            preservable=False,
            dependency_challenge_required=observation.expected_pair_count > 0,
            source=observation.source,
            reason_code="observation_not_structurally_admissible",
        )

    if observation.expected_pair_count == 0:
        return DependencyGraphProof(
            premise_digest=observation.premise_digest,
            edges=observation.edges,
            maturity=DependencyProofMaturity.AUTHORITATIVE,
            observation_count=previous_count + 1,
            preservable=True,
            dependency_challenge_required=False,
            source=observation.source,
            reason_code="single_goal_no_dependency_pair",
        )

    if (
        not same_premise
        or previous is None
        or previous.maturity in {
            DependencyProofMaturity.CANDIDATE,
            DependencyProofMaturity.REJECTED,
            DependencyProofMaturity.STALE,
        }
    ):
        return DependencyGraphProof(
            premise_digest=observation.premise_digest,
            edges=observation.edges,
            maturity=DependencyProofMaturity.VERIFIED,
            observation_count=previous_count + 1,
            preservable=True,
            dependency_challenge_required=True,
            source=observation.source,
            reason_code="first_complete_candidate_blind_graph_requires_adversarial_closure",
        )

    return DependencyGraphProof(
        premise_digest=observation.premise_digest,
        edges=observation.edges,
        maturity=DependencyProofMaturity.AUTHORITATIVE,
        observation_count=previous.observation_count + 1,
        preservable=True,
        dependency_challenge_required=False,
        source=observation.source,
        reason_code=(
            "adversarial_graph_changed_with_grounded_counterevidence"
            if previous.edges != observation.edges
            else "adversarial_graph_confirmed"
        ),
    )


def preserve_dependency_proof(
    proof: DependencyGraphProof | None,
    *,
    premise_digest: str,
) -> DependencyGraphProof | None:
    """Preserve proof only while its frozen semantic premises are unchanged."""

    if proof is None:
        return None
    if proof.premise_digest == premise_digest:
        return proof
    return DependencyGraphProof(
        premise_digest=proof.premise_digest,
        edges=proof.edges,
        maturity=DependencyProofMaturity.STALE,
        observation_count=proof.observation_count,
        preservable=False,
        dependency_challenge_required=proof.dependency_challenge_required,
        source=proof.source,
        reason_code="premise_changed",
    )


def dependency_edge_pairs(details: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    pairs: set[tuple[str, str]] = set()
    for raw in list(details.get("dependency_edges") or []):
        if not isinstance(raw, dict):
            continue
        dependent = str(raw.get("dependent_goal_id") or "").strip()
        prerequisite = str(raw.get("requires_result_of_goal_id") or "").strip()
        if dependent and prerequisite:
            pairs.add((dependent, prerequisite))
    return tuple(sorted(pairs))


def dependency_observation_from_details(
    *,
    premise_digest: str,
    details: dict[str, Any],
    source: str,
) -> DependencyGraphObservation:
    rows = list(details.get("dependency_pair_decisions") or [])
    return DependencyGraphObservation(
        premise_digest=premise_digest,
        edges=dependency_edge_pairs(details),
        complete=details.get("dependency_proof_complete") is True,
        graph_matches_declaration=details.get("dependency_graph_match") is True,
        expected_pair_count=int(details.get("expected_pair_count") or 0),
        observed_pair_count=sum(1 for row in rows if isinstance(row, dict)),
        source=str(source or "candidate_blind_pairwise"),
    )


def dependency_proof_metadata(
    details: dict[str, Any],
    proof: DependencyGraphProof,
) -> dict[str, Any]:
    return {
        **details,
        "dependency_proof_protocol": DEPENDENCY_PROOF_PROTOCOL_VERSION,
        "dependency_authority_state": proof.maturity.value,
        "dependency_authority_preservable": proof.preservable,
        "dependency_challenge_required": proof.dependency_challenge_required,
        "dependency_observation_count": proof.observation_count,
        "dependency_authority_premise_digest": proof.premise_digest,
        "dependency_authority_reason": proof.reason_code,
    }


def candidate_dependency_metadata(
    details: dict[str, Any],
    *,
    premise_digest: str,
    multi_goal: bool,
) -> dict[str, Any]:
    return {
        **details,
        "dependency_proof_protocol": DEPENDENCY_PROOF_PROTOCOL_VERSION,
        "dependency_authority_state": DependencyProofMaturity.CANDIDATE.value,
        "dependency_authority_preservable": False,
        "dependency_challenge_required": bool(multi_goal),
        "dependency_observation_count": 0,
        "dependency_authority_premise_digest": premise_digest,
        "dependency_authority_reason": "candidate_visible_observation_not_authority",
    }


__all__ = [
    "DEPENDENCY_PROOF_PROTOCOL_VERSION",
    "DependencyGraphObservation",
    "DependencyGraphProof",
    "DependencyProofMaturity",
    "candidate_dependency_metadata",
    "dependency_edge_pairs",
    "dependency_observation_from_details",
    "dependency_premise_digest",
    "dependency_proof_metadata",
    "preserve_dependency_proof",
    "reduce_dependency_graph_proof",
]
