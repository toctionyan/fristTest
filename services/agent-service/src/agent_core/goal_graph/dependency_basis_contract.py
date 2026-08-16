from __future__ import annotations

"""Canonical structural contract for current-turn Goal dependency basis evidence.

This module owns only the structural meaning of dependency-basis evidence and the
text projections consumed by semantic verifiers.  It deliberately has no API
that can write ``depends_on`` edges.  Final dependency authority remains the
existing deterministic dependency-proof reducer.
"""

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable, Mapping

CONTRACT_SCHEMA = "dependency-basis-structural-contract@1"
CONTRACT_ID = "customer-agent/dependency-basis@1"
FINAL_DEPENDENCY_AUTHORITY = "deterministic_dependency_proof_reducer"
ALLOWED_DEPENDENCY_BASIS_KINDS = frozenset(
    {
        "result_reference",
        "result_condition",
        "result_value_input",
    }
)

_CANONICAL_CONTRACT: dict[str, Any] = {
    "schema": CONTRACT_SCHEMA,
    "contract_id": CONTRACT_ID,
    "version": 1,
    "authority": {
        "authority_effect": False,
        "final_dependency_authority": FINAL_DEPENDENCY_AUTHORITY,
    },
    "basis": {
        "allowed_kinds": sorted(ALLOWED_DEPENDENCY_BASIS_KINDS),
        "relation_only_required": True,
    },
    "rules": {
        "strict_nested_requested_output": "allowed",
        "requested_output_equality": "forbidden",
        "requested_output_wrapper": "forbidden",
        "no_valid_relation_only_basis": "independent",
    },
}


def canonical_contract() -> dict[str, Any]:
    """Return a detached copy so callers cannot mutate canonical authority."""

    return deepcopy(_CANONICAL_CONTRACT)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def contract_fingerprint(payload: Mapping[str, Any] | None = None) -> str:
    value = _CANONICAL_CONTRACT if payload is None else payload
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def validate_contract(payload: Mapping[str, Any] | None = None) -> list[str]:
    """Fail closed when any semantic or authority field drifts."""

    value = _CANONICAL_CONTRACT if payload is None else dict(payload)
    errors: list[str] = []
    if value != _CANONICAL_CONTRACT:
        errors.append("canonical_contract_drift")

    authority = value.get("authority") if isinstance(value.get("authority"), dict) else {}
    if authority.get("authority_effect") is not False:
        errors.append("canonical_contract_gained_authority")
    if authority.get("final_dependency_authority") != FINAL_DEPENDENCY_AUTHORITY:
        errors.append("final_dependency_authority_drift")

    basis = value.get("basis") if isinstance(value.get("basis"), dict) else {}
    if basis.get("allowed_kinds") != sorted(ALLOWED_DEPENDENCY_BASIS_KINDS):
        errors.append("basis_kind_contract_drift")
    if basis.get("relation_only_required") is not True:
        errors.append("relation_only_requirement_drift")

    rules = value.get("rules") if isinstance(value.get("rules"), dict) else {}
    expected_rules = _CANONICAL_CONTRACT["rules"]
    for key, expected in expected_rules.items():
        if rules.get(key) != expected:
            errors.append(f"rule_drift:{key}")
    return list(dict.fromkeys(errors))


def dependency_basis_conflicts_with_requested_outputs(
    basis_span: str,
    requested_output_spans: Iterable[str],
) -> bool:
    """Return True only for equality or a basis that wraps requested output.

    A strictly smaller literal relation-only basis may be nested inside a broader
    requested-output span.  Whether the basis is *semantically* relation-only is
    still judged by the semantic verifier; this function performs only the
    canonical structural containment check.
    """

    basis = str(basis_span or "").strip()
    if not basis:
        return False
    for raw in requested_output_spans:
        output = str(raw or "").strip()
        if not output:
            continue
        if basis == output or output in basis:
            return True
    return False


def _projection_provenance() -> str:
    return f"[contract={CONTRACT_ID} sha256={contract_fingerprint()}]"


def render_candidate_blind_dependency_rule() -> str:
    """Render the canonical dependency rule for the blind semantic verifier."""

    return (
        f"{_projection_provenance()} "
        "dependency basis evidence must identify only the result-reference, "
        "result-condition or result-value-input relation itself; it must be "
        "relation-only, must not equal a requested-output evidence span, and "
        "must not wrap a requested-output evidence span with action/control wording. "
        "A strictly smaller relation-only literal basis nested inside a broader "
        "requested-output evidence span is admissible because the basis itself "
        "denotes only the result-reference, result-condition or result-value-input "
        "relation; use the smallest such literal basis when one exists, otherwise "
        "the pair is independent"
    )


def render_dependency_format_repair_rule() -> str:
    """Render the same canonical rule for structured-output format repair."""

    return (
        f"{_projection_provenance()} "
        "The basis must be relation-only, must not equal a requested-output evidence "
        "span, and must not wrap a requested-output evidence span with action/control "
        "wording; a strictly smaller relation-only literal basis nested inside a "
        "broader requested-output evidence span is admissible. If no valid "
        "relation-only basis exists under those rules, return relation=independent."
    )


def projection_manifest() -> dict[str, Any]:
    """Deterministic provenance record for every semantic text projection."""

    return {
        "schema": "dependency-basis-projection-manifest@1",
        "contract_id": CONTRACT_ID,
        "contract_sha256": contract_fingerprint(),
        "final_dependency_authority": FINAL_DEPENDENCY_AUTHORITY,
        "authority_effect": False,
        "projections": {
            "candidate_blind_dependency_rule": render_candidate_blind_dependency_rule(),
            "format_repair_dependency_rule": render_dependency_format_repair_rule(),
        },
    }


def verify_projection_manifest(payload: Mapping[str, Any]) -> bool:
    return dict(payload) == projection_manifest()


def mutation_detection_matrix() -> dict[str, bool]:
    """Prove the guards reject representative semantic/authority drift."""

    results: dict[str, bool] = {}

    contract_mutations = {
        "nested_rule_flipped": ("rules", "strict_nested_requested_output", "forbidden"),
        "equality_rule_flipped": ("rules", "requested_output_equality", "allowed"),
        "wrapper_rule_flipped": ("rules", "requested_output_wrapper", "allowed"),
        "relation_only_disabled": ("basis", "relation_only_required", False),
        "authority_changed": (
            "authority",
            "final_dependency_authority",
            "model_goal_alignment_verifier",
        ),
        "authority_effect_enabled": ("authority", "authority_effect", True),
    }
    for name, (section, field, value) in contract_mutations.items():
        mutated = canonical_contract()
        mutated[section][field] = value
        results[name] = bool(validate_contract(mutated))

    manifest = projection_manifest()
    drifted_text = deepcopy(manifest)
    drifted_text["projections"]["candidate_blind_dependency_rule"] += " DRIFT"
    results["projection_text_drift"] = not verify_projection_manifest(drifted_text)

    drifted_hash = deepcopy(manifest)
    drifted_hash["contract_sha256"] = "0" * 64
    results["projection_hash_drift"] = not verify_projection_manifest(drifted_hash)

    return results
