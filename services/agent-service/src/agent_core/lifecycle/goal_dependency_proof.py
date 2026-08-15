from __future__ import annotations

"""Pure maturity reducer for semantic dependency proof observations.

The model verifier produces observations; this module decides only proof
maturity. It deliberately owns no request-local/global session and never wraps
or mutates the Goal-alignment parser helpers. Request orchestration keeps the
current proof in local verifier state and feeds observations into these pure
functions.

Authority is role-driven, never call-count driven:

* a complete candidate-blind pairwise graph is VERIFIED and preservable;
* only an explicit dependency-closure observation may promote VERIFIED proof to
  AUTHORITATIVE;
* semantic-only adjudication does not participate in dependency maturity;
* AUTHORITATIVE proof cannot be replaced by another opinion. A replacement
  requires new admissible counterevidence bound to the current authority and an
  independent reclosure observation;
* changed frozen premises stale the old proof and require a fresh sequence.

`complete` and `matching` are diagnostics. They do not mint authority.
"""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any


DEPENDENCY_PROOF_PROTOCOL_VERSION = "semantic-dependency-proof-authority@4"


class DependencyProofMaturity(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    CHALLENGED = "challenged"
    AUTHORITATIVE = "authoritative"
    STALE = "stale"
    REJECTED = "rejected"


class DependencyObservationRole(StrEnum):
    PROVISIONAL = "provisional"
    ADVERSARIAL_CLOSURE = "adversarial_closure"
    COUNTEREVIDENCE = "counterevidence"
    RECLOSURE = "reclosure"


_PROVISIONAL_SOURCES = frozenset({
    "candidate_blind_dependency_reaudit",
    "candidate_blind_dependency_format_repair",
    "candidate_blind_dependency_historical_reference_reaudit",
})
_ADVERSARIAL_CLOSURE_SOURCES = frozenset({
    "candidate_blind_dependency_positive_edge_adjudication",
    "candidate_blind_dependency_independence_adjudication",
    "candidate_blind_dependency_effect_collision_adjudication",
    "candidate_blind_dependency_authority_closure",
})


@dataclass(frozen=True)
class DependencyGraphObservation:
    premise_digest: str
    edges: tuple[tuple[str, str], ...]
    complete: bool
    graph_matches_declaration: bool
    expected_pair_count: int
    observed_pair_count: int
    source: str
    role: DependencyObservationRole = DependencyObservationRole.PROVISIONAL
    evidence_digest: str = ""
    supersedes_evidence_digest: str | None = None

    @property
    def structurally_admissible(self) -> bool:
        # A graph may disagree with Planner and still be valid proof evidence;
        # graph_matches_declaration therefore is metadata, not admissibility.
        return bool(
            self.premise_digest
            and self.complete
            and self.expected_pair_count == self.observed_pair_count
            and (self.expected_pair_count == 0 or self.evidence_digest)
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
    evidence_digest: str = ""
    authority_evidence_digest: str | None = None
    prior_authority_edges: tuple[tuple[str, str], ...] = ()
    prior_authority_evidence_digest: str | None = None
    challenge_edges: tuple[tuple[str, str], ...] = ()
    challenge_evidence_digest: str | None = None

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


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


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
    return _digest({
        "user_text": str(user_text or ""),
        "goals": projected_goals,
    })


def _preserve(
    previous: DependencyGraphProof,
    observation: DependencyGraphObservation,
    *,
    reason_code: str,
) -> DependencyGraphProof:
    return DependencyGraphProof(
        premise_digest=previous.premise_digest,
        edges=previous.edges,
        maturity=previous.maturity,
        observation_count=previous.observation_count + 1,
        preservable=previous.preservable,
        dependency_challenge_required=previous.dependency_challenge_required,
        source=observation.source,
        reason_code=reason_code,
        evidence_digest=previous.evidence_digest,
        authority_evidence_digest=previous.authority_evidence_digest,
        prior_authority_edges=previous.prior_authority_edges,
        prior_authority_evidence_digest=previous.prior_authority_evidence_digest,
        challenge_edges=previous.challenge_edges,
        challenge_evidence_digest=previous.challenge_evidence_digest,
    )


def _verified_from(observation: DependencyGraphObservation, *, count: int, reason_code: str) -> DependencyGraphProof:
    return DependencyGraphProof(
        premise_digest=observation.premise_digest,
        edges=observation.edges,
        maturity=DependencyProofMaturity.VERIFIED,
        observation_count=count,
        preservable=True,
        dependency_challenge_required=True,
        source=observation.source,
        reason_code=reason_code,
        evidence_digest=observation.evidence_digest,
    )


def _authoritative_from(
    observation: DependencyGraphObservation,
    *,
    count: int,
    reason_code: str,
    prior_authority_edges: tuple[tuple[str, str], ...] = (),
    prior_authority_evidence_digest: str | None = None,
) -> DependencyGraphProof:
    return DependencyGraphProof(
        premise_digest=observation.premise_digest,
        edges=observation.edges,
        maturity=DependencyProofMaturity.AUTHORITATIVE,
        observation_count=count,
        preservable=True,
        dependency_challenge_required=False,
        source=observation.source,
        reason_code=reason_code,
        evidence_digest=observation.evidence_digest,
        authority_evidence_digest=observation.evidence_digest or None,
        prior_authority_edges=prior_authority_edges,
        prior_authority_evidence_digest=prior_authority_evidence_digest,
    )


def reduce_dependency_graph_proof(
    previous: DependencyGraphProof | None,
    observation: DependencyGraphObservation,
) -> DependencyGraphProof:
    """Reduce one observation into deterministic dependency proof maturity.

    The reducer never interprets user language and never uses observation number
    as semantic meaning. The orchestration labels each dependency observation by
    role. Only an explicit adversarial-closure role may close a VERIFIED graph.
    """

    same_premise = bool(
        previous is not None
        and previous.premise_digest == observation.premise_digest
    )
    previous_count = previous.observation_count if previous is not None else 0

    if previous is not None and not same_premise:
        if not observation.structurally_admissible:
            return DependencyGraphProof(
                premise_digest=observation.premise_digest,
                edges=observation.edges,
                maturity=DependencyProofMaturity.REJECTED,
                observation_count=previous_count + 1,
                preservable=False,
                dependency_challenge_required=observation.expected_pair_count > 0,
                source=observation.source,
                reason_code="new_premise_observation_not_structurally_admissible",
                evidence_digest=observation.evidence_digest,
            )
        if observation.expected_pair_count == 0:
            return _authoritative_from(
                observation,
                count=previous_count + 1,
                reason_code="single_goal_no_dependency_pair",
            )
        return _verified_from(
            observation,
            count=previous_count + 1,
            reason_code="new_premise_requires_fresh_adversarial_closure",
        )

    if previous is not None and previous.authoritative:
        if not observation.structurally_admissible:
            return _preserve(
                previous,
                observation,
                reason_code="inadmissible_observation_cannot_downgrade_authority",
            )
        if observation.edges == previous.edges:
            return _preserve(
                previous,
                observation,
                reason_code="authoritative_graph_preserved_under_repeat_observation",
            )
        if observation.role != DependencyObservationRole.COUNTEREVIDENCE:
            return _preserve(
                previous,
                observation,
                reason_code="unbound_authoritative_graph_revote_rejected",
            )
        if (
            not observation.supersedes_evidence_digest
            or observation.supersedes_evidence_digest != previous.authority_evidence_digest
        ):
            return _preserve(
                previous,
                observation,
                reason_code="counterevidence_not_bound_to_current_authority",
            )
        if observation.evidence_digest == previous.authority_evidence_digest:
            return _preserve(
                previous,
                observation,
                reason_code="counterevidence_not_new",
            )
        return DependencyGraphProof(
            premise_digest=previous.premise_digest,
            edges=previous.edges,
            maturity=DependencyProofMaturity.CHALLENGED,
            observation_count=previous.observation_count + 1,
            preservable=False,
            dependency_challenge_required=True,
            source=observation.source,
            reason_code="new_bound_counterevidence_requires_reclosure",
            evidence_digest=previous.evidence_digest,
            authority_evidence_digest=previous.authority_evidence_digest,
            prior_authority_edges=previous.edges,
            prior_authority_evidence_digest=previous.authority_evidence_digest,
            challenge_edges=observation.edges,
            challenge_evidence_digest=observation.evidence_digest,
        )

    if previous is not None and previous.maturity == DependencyProofMaturity.CHALLENGED:
        if not observation.structurally_admissible:
            return _preserve(
                previous,
                observation,
                reason_code="inadmissible_observation_cannot_close_challenge",
            )
        if observation.role != DependencyObservationRole.RECLOSURE:
            return _preserve(
                previous,
                observation,
                reason_code="challenge_requires_explicit_reclosure",
            )
        if (
            observation.edges == previous.challenge_edges
            and observation.evidence_digest
            and observation.evidence_digest != previous.challenge_evidence_digest
        ):
            return _authoritative_from(
                observation,
                count=previous.observation_count + 1,
                reason_code="challenge_reclosed_with_distinct_evidence",
                prior_authority_edges=previous.prior_authority_edges,
                prior_authority_evidence_digest=previous.prior_authority_evidence_digest,
            )
        if (
            observation.edges == previous.prior_authority_edges
            and observation.evidence_digest
            and observation.evidence_digest != previous.challenge_evidence_digest
        ):
            return _authoritative_from(
                observation,
                count=previous.observation_count + 1,
                reason_code="prior_authority_reclosed_after_challenge",
            )
        return _preserve(
            previous,
            observation,
            reason_code="challenge_not_reclosed",
        )

    if not observation.structurally_admissible:
        if same_premise and previous is not None and previous.preservable:
            return _preserve(
                previous,
                observation,
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
            evidence_digest=observation.evidence_digest,
        )

    if observation.expected_pair_count == 0:
        return _authoritative_from(
            observation,
            count=previous_count + 1,
            reason_code="single_goal_no_dependency_pair",
        )

    if (
        previous is None
        or previous.maturity in {
            DependencyProofMaturity.CANDIDATE,
            DependencyProofMaturity.REJECTED,
            DependencyProofMaturity.STALE,
        }
    ):
        return _verified_from(
            observation,
            count=previous_count + 1,
            reason_code="first_complete_candidate_blind_graph_requires_adversarial_closure",
        )

    if previous.maturity == DependencyProofMaturity.VERIFIED:
        if observation.role != DependencyObservationRole.ADVERSARIAL_CLOSURE:
            return _verified_from(
                observation,
                count=previous.observation_count + 1,
                reason_code="provisional_reaudit_does_not_mint_authority",
            )
        return _authoritative_from(
            observation,
            count=previous.observation_count + 1,
            reason_code=(
                "adversarial_graph_changed_with_grounded_counterevidence"
                if previous.edges != observation.edges
                else "adversarial_graph_confirmed"
            ),
        )

    return DependencyGraphProof(
        premise_digest=observation.premise_digest,
        edges=observation.edges,
        maturity=DependencyProofMaturity.REJECTED,
        observation_count=previous_count + 1,
        preservable=False,
        dependency_challenge_required=observation.expected_pair_count > 0,
        source=observation.source,
        reason_code="unsupported_dependency_proof_transition",
        evidence_digest=observation.evidence_digest,
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
        evidence_digest=proof.evidence_digest,
        authority_evidence_digest=proof.authority_evidence_digest,
        prior_authority_edges=proof.prior_authority_edges,
        prior_authority_evidence_digest=proof.prior_authority_evidence_digest,
        challenge_edges=proof.challenge_edges,
        challenge_evidence_digest=proof.challenge_evidence_digest,
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


def _observation_role_for_source(source: str) -> DependencyObservationRole:
    normalized = str(source or "").strip()
    if normalized in _ADVERSARIAL_CLOSURE_SOURCES:
        return DependencyObservationRole.ADVERSARIAL_CLOSURE
    if normalized in _PROVISIONAL_SOURCES:
        return DependencyObservationRole.PROVISIONAL
    return DependencyObservationRole.PROVISIONAL


def _pairwise_evidence_digest(details: dict[str, Any]) -> str:
    rows = []
    for raw in list(details.get("dependency_pair_decisions") or []):
        if not isinstance(raw, dict):
            continue
        rows.append({
            "goal_a_id": str(raw.get("goal_a_id") or "").strip(),
            "goal_b_id": str(raw.get("goal_b_id") or "").strip(),
            "relation": str(raw.get("relation") or "").strip().casefold(),
            "basis_kind": str(raw.get("basis_kind") or "").strip().casefold() or None,
            "basis_span": str(raw.get("basis_span") or "").strip() or None,
        })
    rows.sort(key=lambda row: (
        min(row["goal_a_id"], row["goal_b_id"]),
        max(row["goal_a_id"], row["goal_b_id"]),
    ))
    return _digest(rows) if rows else ""


def dependency_observation_from_details(
    *,
    premise_digest: str,
    details: dict[str, Any],
    source: str,
    role: DependencyObservationRole | None = None,
    supersedes_evidence_digest: str | None = None,
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
        role=role or _observation_role_for_source(source),
        evidence_digest=_pairwise_evidence_digest(details),
        supersedes_evidence_digest=(
            str(supersedes_evidence_digest or "").strip() or None
        ),
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
        "dependency_evidence_digest": proof.evidence_digest or None,
        "dependency_authority_evidence_digest": proof.authority_evidence_digest,
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
        "dependency_evidence_digest": None,
        "dependency_authority_evidence_digest": None,
    }


__all__ = [
    "DEPENDENCY_PROOF_PROTOCOL_VERSION",
    "DependencyGraphObservation",
    "DependencyGraphProof",
    "DependencyObservationRole",
    "DependencyProofMaturity",
    "candidate_dependency_metadata",
    "dependency_edge_pairs",
    "dependency_observation_from_details",
    "dependency_premise_digest",
    "dependency_proof_metadata",
    "preserve_dependency_proof",
    "reduce_dependency_graph_proof",
]
