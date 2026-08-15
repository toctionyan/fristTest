from __future__ import annotations

"""Deterministic dependency-proof maturity and authority reducer.

The language/model boundary may propose dependency observations. This module
never interprets user language, chooses tools, or mutates business state. It
only validates sealed observation structure, evaluates explicit proof
obligations, and controls when a pairwise dependency claim may become (or stop
being) authority.

The central invariant is deliberately asymmetric:

* before authority, a claim must satisfy every required proof obligation;
* after authority, repeated opinions cannot downgrade it. A downgrade requires
  new admissible counterevidence bound to the exact authority evidence, or a
  change to the frozen premise digest.

``complete`` and ``matching`` are retained only as diagnostic claims. They are
never proof obligations and can never mint authority by themselves.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Iterable

DEPENDENCY_PROOF_OBSERVATION_VERSION = "dependency-proof-observation@1"
DEPENDENCY_PROOF_STATE_VERSION = "dependency-proof-state@1"
DEPENDENCY_PROOF_LEDGER_VERSION = "dependency-proof-ledger@1"

DEPENDENCY_PRESENT = "PRESENT"
DEPENDENCY_ABSENT = "ABSENT"

RELATION_A_DEPENDS_ON_B = "a_depends_on_b"
RELATION_B_DEPENDS_ON_A = "b_depends_on_a"
RELATION_INDEPENDENT = "independent"
_ALLOWED_RELATIONS = {
    RELATION_A_DEPENDS_ON_B,
    RELATION_B_DEPENDS_ON_A,
    RELATION_INDEPENDENT,
}
_ALLOWED_BASIS_KINDS = {
    "result_reference",
    "result_condition",
    "result_value_input",
}

UNKNOWN = "UNKNOWN"
CANDIDATE = "CANDIDATE"
GROUNDED = "GROUNDED"
CHALLENGED = "CHALLENGED"
VERIFIED = "VERIFIED"
AUTHORITATIVE = "AUTHORITATIVE"
STALE = "STALE"
REJECTED = "REJECTED"
_ALLOWED_MATURITIES = {
    UNKNOWN,
    CANDIDATE,
    GROUNDED,
    CHALLENGED,
    VERIFIED,
    AUTHORITATIVE,
    STALE,
    REJECTED,
}

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN_RESULT = "UNKNOWN"
_ALLOWED_OBLIGATION_RESULTS = {PASS, FAIL, UNKNOWN_RESULT}
_BASE_REQUIRED_OBLIGATIONS = (
    "grounding",
    "semantic_compatibility",
    "target_compatibility",
    "counterfactual",
    "structural_validity",
    "contradiction_free",
)


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _sealed_digest(row: dict[str, Any], field: str) -> str:
    payload = deepcopy(row)
    payload.pop(field, None)
    return canonical_digest(payload)


def _canonical_pair(goal_a_id: str, goal_b_id: str) -> tuple[str, str, bool]:
    left = _text(goal_a_id, limit=200)
    right = _text(goal_b_id, limit=200)
    if not left or not right or left == right:
        raise ValueError("DEPENDENCY_PAIR_INVALID")
    if left < right:
        return left, right, False
    return right, left, True


def _canonical_relation(relation: str, *, swapped: bool) -> str:
    value = _text(relation, limit=80).casefold()
    if value not in _ALLOWED_RELATIONS:
        raise ValueError("DEPENDENCY_RELATION_INVALID")
    if not swapped or value == RELATION_INDEPENDENT:
        return value
    if value == RELATION_A_DEPENDS_ON_B:
        return RELATION_B_DEPENDS_ON_A
    return RELATION_A_DEPENDS_ON_B


def dependency_pair_key(goal_a_id: str, goal_b_id: str) -> str:
    left, right, _ = _canonical_pair(goal_a_id, goal_b_id)
    return f"{left}::{right}"


def _claim_polarity(relation: str) -> str:
    return DEPENDENCY_ABSENT if relation == RELATION_INDEPENDENT else DEPENDENCY_PRESENT


def _required_obligations(relation: str) -> tuple[str, ...]:
    if relation == RELATION_INDEPENDENT:
        return (*_BASE_REQUIRED_OBLIGATIONS, "pair_coverage")
    return _BASE_REQUIRED_OBLIGATIONS


def _normalize_obligations(obligations: dict[str, Any] | None) -> dict[str, str]:
    source = obligations if isinstance(obligations, dict) else {}
    normalized: dict[str, str] = {}
    for key, value in source.items():
        name = _text(key, limit=120)
        result = _text(value, limit=40).upper()
        if name:
            normalized[name] = result if result in _ALLOWED_OBLIGATION_RESULTS else UNKNOWN_RESULT
    return normalized


def make_dependency_observation(
    *,
    goal_a_id: str,
    goal_b_id: str,
    relation: str,
    premise_digest: str,
    evidence_payload: dict[str, Any],
    obligations: dict[str, Any],
    grounding_proof_digest: str,
    counterfactual_proof_digest: str,
    source: str,
    basis_kind: str | None = None,
    basis_span: str | None = None,
    semantic_contract_id: str | None = None,
    semantic_digest: str | None = None,
    supersedes_evidence_digest: str | None = None,
    diagnostic_complete: bool | None = None,
    diagnostic_matching: bool | None = None,
) -> dict[str, Any]:
    """Build a sealed observation. It is evidence, never authority."""

    left, right, swapped = _canonical_pair(goal_a_id, goal_b_id)
    canonical_relation = _canonical_relation(relation, swapped=swapped)
    normalized_obligations = _normalize_obligations(obligations)
    evidence = deepcopy(evidence_payload) if isinstance(evidence_payload, dict) else {}
    evidence_digest = canonical_digest(evidence)

    row: dict[str, Any] = {
        "version": DEPENDENCY_PROOF_OBSERVATION_VERSION,
        "goal_a_id": left,
        "goal_b_id": right,
        "pair_key": f"{left}::{right}",
        "relation": canonical_relation,
        "claim_polarity": _claim_polarity(canonical_relation),
        "premise_digest": _text(premise_digest, limit=128),
        "semantic_contract_id": _text(semantic_contract_id, limit=500) or None,
        "semantic_digest": _text(semantic_digest, limit=128) or None,
        "basis_kind": _text(basis_kind, limit=80).casefold() or None,
        "basis_span": _text(basis_span, limit=240) or None,
        "grounding_proof_digest": _text(grounding_proof_digest, limit=256),
        "counterfactual_proof_digest": _text(counterfactual_proof_digest, limit=256),
        "evidence": evidence,
        "evidence_digest": evidence_digest,
        "obligations": normalized_obligations,
        "source": _text(source, limit=200) or "unspecified",
        "supersedes_evidence_digest": _text(supersedes_evidence_digest, limit=128) or None,
        "diagnostic_claims": {
            "complete": diagnostic_complete if isinstance(diagnostic_complete, bool) else None,
            "matching": diagnostic_matching if isinstance(diagnostic_matching, bool) else None,
            "authority_effect": False,
        },
    }
    row["observation_id"] = f"dependency-observation:{canonical_digest(row)[:24]}"
    row["observation_digest"] = _sealed_digest(row, "observation_digest")
    return row


def dependency_observation_integrity(observation: dict[str, Any] | None) -> dict[str, Any]:
    row = deepcopy(observation) if isinstance(observation, dict) else {}
    errors: list[str] = []
    if row.get("version") != DEPENDENCY_PROOF_OBSERVATION_VERSION:
        errors.append("DEPENDENCY_OBSERVATION_VERSION_INVALID")

    goal_a = _text(row.get("goal_a_id"), limit=200)
    goal_b = _text(row.get("goal_b_id"), limit=200)
    if not goal_a or not goal_b or goal_a >= goal_b:
        errors.append("DEPENDENCY_OBSERVATION_PAIR_NOT_CANONICAL")
    if _text(row.get("pair_key"), limit=500) != (f"{goal_a}::{goal_b}" if goal_a and goal_b else ""):
        errors.append("DEPENDENCY_OBSERVATION_PAIR_KEY_INVALID")

    relation = _text(row.get("relation"), limit=80).casefold()
    if relation not in _ALLOWED_RELATIONS:
        errors.append("DEPENDENCY_OBSERVATION_RELATION_INVALID")
    polarity = _text(row.get("claim_polarity"), limit=40).upper()
    if relation in _ALLOWED_RELATIONS and polarity != _claim_polarity(relation):
        errors.append("DEPENDENCY_OBSERVATION_POLARITY_INVALID")

    if not _text(row.get("premise_digest"), limit=128):
        errors.append("DEPENDENCY_OBSERVATION_PREMISE_REQUIRED")
    if not _text(row.get("grounding_proof_digest"), limit=256):
        errors.append("DEPENDENCY_OBSERVATION_GROUNDING_PROOF_REQUIRED")
    if not _text(row.get("counterfactual_proof_digest"), limit=256):
        errors.append("DEPENDENCY_OBSERVATION_COUNTERFACTUAL_PROOF_REQUIRED")

    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    if _text(row.get("evidence_digest"), limit=128) != canonical_digest(evidence):
        errors.append("DEPENDENCY_OBSERVATION_EVIDENCE_DIGEST_INVALID")

    if relation in {RELATION_A_DEPENDS_ON_B, RELATION_B_DEPENDS_ON_A}:
        if _text(row.get("basis_kind"), limit=80).casefold() not in _ALLOWED_BASIS_KINDS:
            errors.append("DEPENDENCY_OBSERVATION_BASIS_KIND_REQUIRED")
        if not _text(row.get("basis_span"), limit=240):
            errors.append("DEPENDENCY_OBSERVATION_BASIS_SPAN_REQUIRED")
    elif relation == RELATION_INDEPENDENT:
        if row.get("basis_kind") not in (None, "") or row.get("basis_span") not in (None, ""):
            errors.append("DEPENDENCY_ABSENCE_MUST_NOT_FAKE_POSITIVE_BASIS")

    obligations = row.get("obligations") if isinstance(row.get("obligations"), dict) else {}
    if relation in _ALLOWED_RELATIONS:
        for name in _required_obligations(relation):
            if _text(obligations.get(name), limit=40).upper() not in _ALLOWED_OBLIGATION_RESULTS:
                errors.append(f"DEPENDENCY_OBLIGATION_INVALID:{name}")

    claims = row.get("diagnostic_claims") if isinstance(row.get("diagnostic_claims"), dict) else {}
    if claims.get("authority_effect") is not False:
        errors.append("DEPENDENCY_DIAGNOSTIC_CLAIMS_MUST_NOT_GRANT_AUTHORITY")

    observation_id = _text(row.get("observation_id"), limit=500)
    unsigned_for_id = deepcopy(row)
    unsigned_for_id.pop("observation_id", None)
    unsigned_for_id.pop("observation_digest", None)
    expected_id = f"dependency-observation:{canonical_digest(unsigned_for_id)[:24]}"
    if observation_id != expected_id:
        errors.append("DEPENDENCY_OBSERVATION_ID_INVALID")

    stored_digest = _text(row.get("observation_digest"), limit=128)
    if not stored_digest or stored_digest != _sealed_digest(row, "observation_digest"):
        errors.append("DEPENDENCY_OBSERVATION_DIGEST_INVALID")

    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "pair_key": row.get("pair_key"),
        "observation_digest": stored_digest or None,
    }


def _proof_stage(relation: str, obligations: dict[str, str]) -> tuple[str, str]:
    required = _required_obligations(relation)
    results = {name: obligations.get(name, UNKNOWN_RESULT) for name in required}
    if any(value == FAIL for value in results.values()):
        return REJECTED, "REQUIRED_PROOF_OBLIGATION_FAILED"
    if all(value == PASS for value in results.values()):
        return VERIFIED, "ALL_REQUIRED_PROOF_OBLIGATIONS_PASSED"
    if results.get("grounding") == PASS:
        return GROUNDED, "PROOF_GROUNDED_BUT_NOT_CLOSED"
    return CANDIDATE, "PROOF_OBLIGATIONS_INCOMPLETE"


def _seal_state(state: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(state)
    row["version"] = DEPENDENCY_PROOF_STATE_VERSION
    row["state_digest"] = _sealed_digest(row, "state_digest")
    return row


def _fresh_state_from_observation(observation: dict[str, Any]) -> dict[str, Any]:
    relation = str(observation["relation"])
    obligations = dict(observation.get("obligations") or {})
    maturity, reason = _proof_stage(relation, obligations)
    return _seal_state({
        "pair_key": observation["pair_key"],
        "goal_a_id": observation["goal_a_id"],
        "goal_b_id": observation["goal_b_id"],
        "premise_digest": observation["premise_digest"],
        "maturity": maturity,
        "relation": relation,
        "claim_polarity": observation["claim_polarity"],
        "obligations": obligations,
        "latest_observation_digest": observation["observation_digest"],
        "latest_evidence_digest": observation["evidence_digest"],
        "authority_relation": None,
        "authority_evidence_digest": None,
        "authority_digest": None,
        "challenge_relation": None,
        "challenge_evidence_digest": None,
        "challenge_observation_digest": None,
        "prior_authority_relation": None,
        "prior_authority_evidence_digest": None,
        "last_transition": "OBSERVE",
        "reason_code": reason,
        "diagnostic_claims": deepcopy(observation.get("diagnostic_claims") or {}),
    })


def _preserve_authority(
    previous: dict[str, Any],
    observation: dict[str, Any],
    *,
    reason_code: str,
) -> dict[str, Any]:
    row = deepcopy(previous)
    row.update({
        "maturity": AUTHORITATIVE,
        "latest_observation_digest": observation.get("observation_digest"),
        "latest_evidence_digest": observation.get("evidence_digest"),
        "last_transition": "PRESERVE_AUTHORITY",
        "reason_code": reason_code,
        "diagnostic_claims": deepcopy(observation.get("diagnostic_claims") or {}),
    })
    return _seal_state(row)


def reduce_dependency_observation(
    previous_state: dict[str, Any] | None,
    observation: dict[str, Any],
    *,
    current_premise_digest: str,
) -> dict[str, Any]:
    """Pure reducer from one observation to the next pairwise proof state."""

    previous = deepcopy(previous_state) if isinstance(previous_state, dict) else None
    integrity = dependency_observation_integrity(observation)
    current_premise = _text(current_premise_digest, limit=128)

    if previous and previous.get("maturity") == AUTHORITATIVE:
        if _text(previous.get("premise_digest"), limit=128) != current_premise:
            stale = deepcopy(previous)
            stale.update({
                "maturity": STALE,
                "last_transition": "INVALIDATE_PREMISE",
                "reason_code": "AUTHORITATIVE_PREMISE_CHANGED",
                "latest_observation_digest": observation.get("observation_digest"),
            })
            return _seal_state(stale)
        if not integrity["ok"]:
            return _preserve_authority(
                previous,
                observation,
                reason_code="INADMISSIBLE_OBSERVATION_CANNOT_DOWNGRADE_AUTHORITY",
            )
        if _text(observation.get("premise_digest"), limit=128) != current_premise:
            return _preserve_authority(
                previous,
                observation,
                reason_code="OBSERVATION_PREMISE_MISMATCH_CANNOT_DOWNGRADE_AUTHORITY",
            )

        relation = str(observation["relation"])
        if relation == str(previous.get("authority_relation") or previous.get("relation") or ""):
            return _preserve_authority(
                previous,
                observation,
                reason_code="REPEATED_SAME_CLAIM_PRESERVES_AUTHORITY",
            )

        candidate = _fresh_state_from_observation(observation)
        if candidate["maturity"] != VERIFIED:
            return _preserve_authority(
                previous,
                observation,
                reason_code="UNVERIFIED_COUNTERCLAIM_CANNOT_DOWNGRADE_AUTHORITY",
            )
        bound_to = _text(observation.get("supersedes_evidence_digest"), limit=128)
        authority_evidence = _text(previous.get("authority_evidence_digest"), limit=128)
        if not bound_to or bound_to != authority_evidence:
            return _preserve_authority(
                previous,
                observation,
                reason_code="COUNTEREVIDENCE_NOT_BOUND_TO_CURRENT_AUTHORITY",
            )
        if _text(observation.get("evidence_digest"), limit=128) == authority_evidence:
            return _preserve_authority(
                previous,
                observation,
                reason_code="COUNTEREVIDENCE_NOT_NEW",
            )

        challenged = deepcopy(previous)
        challenged.update({
            "maturity": CHALLENGED,
            "prior_authority_relation": previous.get("authority_relation") or previous.get("relation"),
            "prior_authority_evidence_digest": authority_evidence,
            "challenge_relation": relation,
            "challenge_evidence_digest": observation.get("evidence_digest"),
            "challenge_observation_digest": observation.get("observation_digest"),
            "latest_observation_digest": observation.get("observation_digest"),
            "latest_evidence_digest": observation.get("evidence_digest"),
            "last_transition": "CHALLENGE_AUTHORITY",
            "reason_code": "NEW_ADMISSIBLE_COUNTEREVIDENCE_REQUIRES_RECLOSURE",
        })
        return _seal_state(challenged)

    if previous and previous.get("maturity") == CHALLENGED:
        if _text(previous.get("premise_digest"), limit=128) != current_premise:
            stale = deepcopy(previous)
            stale.update({
                "maturity": STALE,
                "last_transition": "INVALIDATE_PREMISE",
                "reason_code": "CHALLENGED_PREMISE_CHANGED",
            })
            return _seal_state(stale)
        if not integrity["ok"] or _text(observation.get("premise_digest"), limit=128) != current_premise:
            held = deepcopy(previous)
            held.update({
                "latest_observation_digest": observation.get("observation_digest"),
                "last_transition": "HOLD_CHALLENGE",
                "reason_code": "INADMISSIBLE_OBSERVATION_CANNOT_CLOSE_CHALLENGE",
            })
            return _seal_state(held)

        candidate = _fresh_state_from_observation(observation)
        relation = str(observation["relation"])
        new_evidence = _text(observation.get("evidence_digest"), limit=128)
        if candidate["maturity"] == VERIFIED:
            if (
                relation == str(previous.get("challenge_relation") or "")
                and new_evidence != _text(previous.get("challenge_evidence_digest"), limit=128)
            ):
                candidate.update({
                    "last_transition": "VERIFY_CHALLENGE",
                    "reason_code": "CHALLENGE_RECLOSED_WITH_DISTINCT_EVIDENCE",
                    "prior_authority_relation": previous.get("prior_authority_relation"),
                    "prior_authority_evidence_digest": previous.get("prior_authority_evidence_digest"),
                })
                return _seal_state(candidate)
            if (
                relation == str(previous.get("prior_authority_relation") or "")
                and new_evidence != _text(previous.get("challenge_evidence_digest"), limit=128)
            ):
                candidate.update({
                    "last_transition": "REVERIFY_PRIOR_AUTHORITY",
                    "reason_code": "PRIOR_AUTHORITY_RECLOSED_AFTER_CHALLENGE",
                })
                return _seal_state(candidate)

        held = deepcopy(previous)
        held.update({
            "latest_observation_digest": observation.get("observation_digest"),
            "latest_evidence_digest": observation.get("evidence_digest"),
            "last_transition": "HOLD_CHALLENGE",
            "reason_code": "CHALLENGE_NOT_RECLOSED",
        })
        return _seal_state(held)

    if not integrity["ok"]:
        pair_key = _text(observation.get("pair_key"), limit=500)
        return _seal_state({
            "pair_key": pair_key or None,
            "goal_a_id": observation.get("goal_a_id"),
            "goal_b_id": observation.get("goal_b_id"),
            "premise_digest": _text(observation.get("premise_digest"), limit=128) or None,
            "maturity": REJECTED,
            "relation": observation.get("relation"),
            "claim_polarity": observation.get("claim_polarity"),
            "obligations": deepcopy(observation.get("obligations") or {}),
            "latest_observation_digest": observation.get("observation_digest"),
            "latest_evidence_digest": observation.get("evidence_digest"),
            "authority_relation": None,
            "authority_evidence_digest": None,
            "authority_digest": None,
            "challenge_relation": None,
            "challenge_evidence_digest": None,
            "challenge_observation_digest": None,
            "prior_authority_relation": None,
            "prior_authority_evidence_digest": None,
            "last_transition": "REJECT_OBSERVATION",
            "reason_code": "OBSERVATION_INADMISSIBLE",
            "integrity_errors": list(integrity["errors"]),
            "diagnostic_claims": deepcopy(observation.get("diagnostic_claims") or {}),
        })

    if _text(observation.get("premise_digest"), limit=128) != current_premise:
        fresh = _fresh_state_from_observation(observation)
        fresh.update({
            "maturity": STALE,
            "last_transition": "REJECT_STALE_PREMISE",
            "reason_code": "OBSERVATION_PREMISE_NOT_CURRENT",
        })
        return _seal_state(fresh)

    return _fresh_state_from_observation(observation)


def _seal_verified_authority(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("maturity") != VERIFIED:
        return _seal_state(state)
    row = deepcopy(state)
    row.update({
        "maturity": AUTHORITATIVE,
        "authority_relation": row.get("relation"),
        "authority_evidence_digest": row.get("latest_evidence_digest"),
        "challenge_relation": None,
        "challenge_evidence_digest": None,
        "challenge_observation_digest": None,
        "last_transition": "SEAL_AUTHORITY",
        "reason_code": "VERIFIED_PROOF_SEALED_AS_AUTHORITY",
    })
    authority_payload = {
        "pair_key": row.get("pair_key"),
        "premise_digest": row.get("premise_digest"),
        "relation": row.get("authority_relation"),
        "evidence_digest": row.get("authority_evidence_digest"),
    }
    row["authority_digest"] = canonical_digest(authority_payload)
    return _seal_state(row)


def make_dependency_proof_ledger() -> dict[str, Any]:
    ledger: dict[str, Any] = {
        "version": DEPENDENCY_PROOF_LEDGER_VERSION,
        "states": {},
        "observation_digests": [],
    }
    ledger["ledger_digest"] = _sealed_digest(ledger, "ledger_digest")
    return ledger


def dependency_proof_ledger_integrity(ledger: dict[str, Any] | None) -> dict[str, Any]:
    row = deepcopy(ledger) if isinstance(ledger, dict) else {}
    errors: list[str] = []
    if row.get("version") != DEPENDENCY_PROOF_LEDGER_VERSION:
        errors.append("DEPENDENCY_LEDGER_VERSION_INVALID")
    states = row.get("states") if isinstance(row.get("states"), dict) else None
    if states is None:
        errors.append("DEPENDENCY_LEDGER_STATES_INVALID")
    else:
        for pair_key, state in states.items():
            if not isinstance(state, dict) or _text(state.get("pair_key"), limit=500) != str(pair_key):
                errors.append(f"DEPENDENCY_LEDGER_STATE_KEY_INVALID:{pair_key}")
                continue
            if state.get("version") != DEPENDENCY_PROOF_STATE_VERSION:
                errors.append(f"DEPENDENCY_LEDGER_STATE_VERSION_INVALID:{pair_key}")
            if _text(state.get("maturity"), limit=40).upper() not in _ALLOWED_MATURITIES:
                errors.append(f"DEPENDENCY_LEDGER_STATE_MATURITY_INVALID:{pair_key}")
            stored_state_digest = _text(state.get("state_digest"), limit=128)
            if not stored_state_digest or stored_state_digest != _sealed_digest(state, "state_digest"):
                errors.append(f"DEPENDENCY_LEDGER_STATE_DIGEST_INVALID:{pair_key}")
    stored = _text(row.get("ledger_digest"), limit=128)
    if not stored or stored != _sealed_digest(row, "ledger_digest"):
        errors.append("DEPENDENCY_LEDGER_DIGEST_INVALID")
    return {"ok": not errors, "errors": sorted(set(errors)), "ledger_digest": stored or None}


def apply_dependency_observation(
    ledger: dict[str, Any] | None,
    observation: dict[str, Any],
    *,
    current_premise_digest: str,
) -> dict[str, Any]:
    """Apply one observation and seal VERIFIED state into authority."""

    if ledger is None:
        current = make_dependency_proof_ledger()
    else:
        current = deepcopy(ledger)
        if not dependency_proof_ledger_integrity(current)["ok"]:
            raise ValueError("DEPENDENCY_LEDGER_INTEGRITY_INVALID")

    pair_key = _text(observation.get("pair_key"), limit=500)
    previous = (current.get("states") or {}).get(pair_key)
    next_state = reduce_dependency_observation(
        previous,
        observation,
        current_premise_digest=current_premise_digest,
    )
    next_state = _seal_verified_authority(next_state)
    current.setdefault("states", {})[pair_key] = next_state

    digest = _text(observation.get("observation_digest"), limit=128)
    if digest and digest not in current.setdefault("observation_digests", []):
        current["observation_digests"].append(digest)
    current["ledger_digest"] = _sealed_digest(current, "ledger_digest")
    return current


def dependency_authority_for_pair(
    ledger: dict[str, Any],
    goal_a_id: str,
    goal_b_id: str,
) -> dict[str, Any] | None:
    pair_key = dependency_pair_key(goal_a_id, goal_b_id)
    state = (ledger.get("states") or {}).get(pair_key)
    if not isinstance(state, dict) or state.get("maturity") != AUTHORITATIVE:
        return None
    return deepcopy(state)


def dependency_graph_from_ledger(
    ledger: dict[str, Any],
    *,
    goal_ids: Iterable[str],
) -> dict[str, Any]:
    """Project a graph only when every unordered pair has authority.

    This is what makes an empty graph proof-carrying: ``edges=[]`` is complete
    only when every pair is AUTHORITATIVE ``independent``.
    """

    ids = sorted({_text(value, limit=200) for value in goal_ids if _text(value, limit=200)})
    expected_pairs = [
        (ids[left], ids[right])
        for left in range(len(ids))
        for right in range(left + 1, len(ids))
    ]
    edges: list[dict[str, str]] = []
    independent_pairs: list[list[str]] = []
    unresolved_pairs: list[list[str]] = []
    authority_digests: list[str] = []

    for goal_a, goal_b in expected_pairs:
        state = dependency_authority_for_pair(ledger, goal_a, goal_b)
        if state is None:
            unresolved_pairs.append([goal_a, goal_b])
            continue
        relation = str(state.get("authority_relation") or state.get("relation") or "")
        authority_digest = _text(state.get("authority_digest"), limit=128)
        if authority_digest:
            authority_digests.append(authority_digest)
        if relation == RELATION_INDEPENDENT:
            independent_pairs.append([goal_a, goal_b])
        elif relation == RELATION_A_DEPENDS_ON_B:
            edges.append({
                "dependent_goal_id": goal_a,
                "requires_result_of_goal_id": goal_b,
            })
        elif relation == RELATION_B_DEPENDS_ON_A:
            edges.append({
                "dependent_goal_id": goal_b,
                "requires_result_of_goal_id": goal_a,
            })
        else:
            unresolved_pairs.append([goal_a, goal_b])

    payload = {
        "complete": not unresolved_pairs,
        "edges": sorted(edges, key=lambda row: (row["dependent_goal_id"], row["requires_result_of_goal_id"])),
        "independent_pairs": independent_pairs,
        "unresolved_pairs": unresolved_pairs,
        "expected_pair_count": len(expected_pairs),
        "authority_digests": sorted(authority_digests),
    }
    payload["graph_proof_digest"] = canonical_digest(payload)
    return payload


def dependency_graph_diff(
    ledger: dict[str, Any],
    *,
    goal_ids: Iterable[str],
    declared_edges: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    graph = dependency_graph_from_ledger(ledger, goal_ids=goal_ids)
    if not graph["complete"]:
        return {
            "repairable": False,
            "reason_code": "DEPENDENCY_AUTHORITY_INCOMPLETE",
            "unresolved_pairs": graph["unresolved_pairs"],
            "graph_proof_digest": graph["graph_proof_digest"],
        }
    expected = {
        (row["dependent_goal_id"], row["requires_result_of_goal_id"])
        for row in graph["edges"]
    }
    declared = {
        (_text(row.get("dependent_goal_id"), limit=200), _text(row.get("requires_result_of_goal_id"), limit=200))
        for row in declared_edges
        if isinstance(row, dict)
        and _text(row.get("dependent_goal_id"), limit=200)
        and _text(row.get("requires_result_of_goal_id"), limit=200)
    }
    return {
        "repairable": True,
        "reason_code": "DEPENDENCY_GRAPH_MATCH" if expected == declared else "DEPENDENCY_GRAPH_MISMATCH",
        "missing_edges": [
            {"dependent_goal_id": dependent, "requires_result_of_goal_id": prerequisite}
            for dependent, prerequisite in sorted(expected - declared)
        ],
        "extra_edges": [
            {"dependent_goal_id": dependent, "requires_result_of_goal_id": prerequisite}
            for dependent, prerequisite in sorted(declared - expected)
        ],
        "authoritative_edges": graph["edges"],
        "graph_proof_digest": graph["graph_proof_digest"],
    }


__all__ = [
    "AUTHORITATIVE",
    "CANDIDATE",
    "CHALLENGED",
    "DEPENDENCY_ABSENT",
    "DEPENDENCY_PRESENT",
    "DEPENDENCY_PROOF_LEDGER_VERSION",
    "DEPENDENCY_PROOF_OBSERVATION_VERSION",
    "DEPENDENCY_PROOF_STATE_VERSION",
    "FAIL",
    "GROUNDED",
    "PASS",
    "REJECTED",
    "RELATION_A_DEPENDS_ON_B",
    "RELATION_B_DEPENDS_ON_A",
    "RELATION_INDEPENDENT",
    "STALE",
    "UNKNOWN",
    "UNKNOWN_RESULT",
    "VERIFIED",
    "apply_dependency_observation",
    "canonical_digest",
    "dependency_authority_for_pair",
    "dependency_graph_diff",
    "dependency_graph_from_ledger",
    "dependency_observation_integrity",
    "dependency_pair_key",
    "dependency_proof_ledger_integrity",
    "make_dependency_observation",
    "make_dependency_proof_ledger",
    "reduce_dependency_observation",
]
