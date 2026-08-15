from __future__ import annotations

"""Deterministic maturity reducer for semantic dependency proof observations.

The model verifier can only submit observations. This module decides whether a
candidate-blind dependency graph is merely observed, verified enough to preserve
while an unrelated semantic claim is re-audited, or authoritative after an
adversarial dependency challenge. It does not choose tools, resolve business
targets, authorize writes, or rewrite Planner declarations.
"""

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from types import ModuleType
from typing import Any, Callable


DEPENDENCY_PROOF_PROTOCOL_VERSION = "semantic-dependency-proof-authority@1"


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


@dataclass
class _DependencyProofSession:
    premise_digest: str
    proof: DependencyGraphProof | None = None


_SESSION: ContextVar[_DependencyProofSession | None] = ContextVar(
    "semantic_dependency_proof_session",
    default=None,
)


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, set):
        return [_canonical(item) for item in sorted(value, key=str)]
    return value


def dependency_premise_digest(*, user_text: str, goals: list[dict[str, Any]]) -> str:
    """Seal semantic premises without making Planner depends_on circular authority."""

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
    payload = {
        "user_text": str(user_text or ""),
        "goals": projected_goals,
    }
    encoded = json.dumps(
        _canonical(payload),
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
    """Reduce one dependency observation into deterministic proof maturity.

    A first complete candidate-blind pairwise proof is VERIFIED and therefore
    preservable against unrelated semantic re-audits, but it is not final
    dependency authority for a multi-Goal graph. A second structurally
    admissible graph observation for the same frozen premises is the bounded
    adversarial dependency challenge and closes the graph as AUTHORITATIVE.
    """

    previous_count = previous.observation_count if previous is not None else 0
    same_premise = bool(
        previous is not None
        and previous.premise_digest == observation.premise_digest
    )

    if not observation.structurally_admissible:
        return DependencyGraphProof(
            premise_digest=observation.premise_digest,
            edges=(previous.edges if same_premise and previous is not None else observation.edges),
            maturity=DependencyProofMaturity.REJECTED,
            observation_count=previous_count + 1,
            preservable=bool(same_premise and previous and previous.preservable),
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

    # This is not a simple majority vote. The existing verifier only reaches a
    # second pairwise observation through its bounded dependency-specific
    # counterfactual/adversarial path. A changed graph therefore carries new
    # grounded counterevidence; an unchanged graph closes the same challenge.
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
    """Preserve mature graph proof across an unrelated semantic-only re-audit."""

    if proof is None:
        return None
    if proof.premise_digest != premise_digest:
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
    return proof


def _edge_pairs(details: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    pairs: set[tuple[str, str]] = set()
    for raw in list(details.get("dependency_edges") or []):
        if not isinstance(raw, dict):
            continue
        dependent = str(raw.get("dependent_goal_id") or "").strip()
        prerequisite = str(raw.get("requires_result_of_goal_id") or "").strip()
        if dependent and prerequisite:
            pairs.add((dependent, prerequisite))
    return tuple(sorted(pairs))


def _observed_pair_count(details: dict[str, Any]) -> int:
    rows = list(details.get("dependency_pair_decisions") or [])
    return sum(1 for row in rows if isinstance(row, dict))


def _proof_metadata(
    details: dict[str, Any],
    proof: DependencyGraphProof | None,
) -> dict[str, Any]:
    if proof is None:
        return details
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


def _reset_session(*, user_text: str, goals: list[dict[str, Any]]) -> _DependencyProofSession:
    session = _DependencyProofSession(
        premise_digest=dependency_premise_digest(user_text=user_text, goals=goals)
    )
    _SESSION.set(session)
    return session


def _session_for(*, user_text: str, goals: list[dict[str, Any]]) -> _DependencyProofSession:
    premise = dependency_premise_digest(user_text=user_text, goals=goals)
    session = _SESSION.get()
    if session is None or session.premise_digest != premise:
        session = _DependencyProofSession(premise_digest=premise)
        _SESSION.set(session)
    return session


def install_goal_dependency_proof_authority(module: ModuleType) -> None:
    """Install one deterministic authority boundary around the existing parser.

    This is an installation seam rather than a second verifier. The original
    verifier keeps producing the same model observations and historical
    diagnostics. Only maturity/authority of structurally validated dependency
    observations is centralized here.
    """

    if bool(getattr(module, "_DEPENDENCY_PROOF_AUTHORITY_INSTALLED", False)):
        return

    original_candidate: Callable[..., tuple[dict[str, Any], str | None]] = getattr(
        module, "_model_alignment_dependency_proof"
    )
    original_pairwise: Callable[..., tuple[dict[str, Any], str | None]] = getattr(
        module, "_model_alignment_pairwise_dependency_proof"
    )

    def candidate_visible_observation(
        *,
        user_text: str,
        goals: list[dict[str, Any]],
        values: Any,
    ) -> tuple[dict[str, Any], str | None]:
        session = _reset_session(user_text=user_text, goals=goals)
        details, error = original_candidate(
            user_text=user_text,
            goals=goals,
            values=values,
        )
        # Candidate-visible output is DISCOVER evidence only. It resets the
        # proof session but can never promote itself to dependency authority.
        tagged = {
            **details,
            "dependency_proof_protocol": DEPENDENCY_PROOF_PROTOCOL_VERSION,
            "dependency_authority_state": DependencyProofMaturity.CANDIDATE.value,
            "dependency_authority_preservable": False,
            "dependency_challenge_required": len(goals) > 1,
            "dependency_observation_count": 0,
            "dependency_authority_premise_digest": session.premise_digest,
            "dependency_authority_reason": "candidate_visible_observation_not_authority",
        }
        return tagged, error

    def candidate_blind_observation(
        *,
        user_text: str,
        goals: list[dict[str, Any]],
        values: Any,
    ) -> tuple[dict[str, Any], str | None]:
        details, error = original_pairwise(
            user_text=user_text,
            goals=goals,
            values=values,
        )
        session = _session_for(user_text=user_text, goals=goals)
        observation = DependencyGraphObservation(
            premise_digest=session.premise_digest,
            edges=_edge_pairs(details),
            complete=details.get("dependency_proof_complete") is True,
            graph_matches_declaration=details.get("dependency_graph_match") is True,
            expected_pair_count=int(details.get("expected_pair_count") or 0),
            observed_pair_count=_observed_pair_count(details),
            source="candidate_blind_pairwise",
        )
        proof = reduce_dependency_graph_proof(session.proof, observation)
        # A malformed observation may not erase an already preservable graph.
        if (
            proof.maturity == DependencyProofMaturity.REJECTED
            and session.proof is not None
            and session.proof.preservable
        ):
            proof_for_metadata = session.proof
        else:
            session.proof = proof
            proof_for_metadata = proof
        return _proof_metadata(details, proof_for_metadata), error

    module._model_alignment_dependency_proof = candidate_visible_observation
    module._model_alignment_pairwise_dependency_proof = candidate_blind_observation
    module._DEPENDENCY_PROOF_AUTHORITY_INSTALLED = True


__all__ = [
    "DEPENDENCY_PROOF_PROTOCOL_VERSION",
    "DependencyGraphObservation",
    "DependencyGraphProof",
    "DependencyProofMaturity",
    "dependency_premise_digest",
    "install_goal_dependency_proof_authority",
    "preserve_dependency_proof",
    "reduce_dependency_graph_proof",
]
