from __future__ import annotations

"""Canonical structural contract for current-turn Goal dependency basis evidence.

This module owns only the structural meaning of dependency-basis evidence and the
text projections consumed by semantic verifiers.  It deliberately has no API
that can write ``depends_on`` edges.  Final dependency authority remains the
existing deterministic dependency-proof reducer.

The structured contract below is the semantic source of truth.  Structural
checks and every prompt projection are compiled from the same payload so a rule
cannot be edited in one representation while silently remaining stale in
another.
"""

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable, Mapping

CONTRACT_SCHEMA = "dependency-basis-structural-contract@2"
CONTRACT_ID = "customer-agent/dependency-basis@2"
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
    "version": 2,
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


def _contract_view(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a detached payload used by pure projection/behavior functions.

    Non-canonical payloads are accepted intentionally for mutation/property
    proofs.  Production callers omit ``payload`` and therefore always consume
    the canonical contract.
    """

    value = _CANONICAL_CONTRACT if payload is None else payload
    return deepcopy(dict(value))


def contract_fingerprint(payload: Mapping[str, Any] | None = None) -> str:
    value = _CANONICAL_CONTRACT if payload is None else payload
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def validate_contract(payload: Mapping[str, Any] | None = None) -> list[str]:
    """Fail closed when any semantic or authority field drifts."""

    value = _contract_view(payload)
    errors: list[str] = []
    if value != _CANONICAL_CONTRACT:
        errors.append("canonical_contract_drift")

    if value.get("schema") != CONTRACT_SCHEMA:
        errors.append("contract_schema_drift")
    if value.get("contract_id") != CONTRACT_ID:
        errors.append("contract_id_drift")
    if value.get("version") != _CANONICAL_CONTRACT["version"]:
        errors.append("contract_version_drift")

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


def _section(payload: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = payload.get(name)
    return value if isinstance(value, Mapping) else {}


def dependency_basis_conflicts_with_requested_outputs(
    basis_span: str,
    requested_output_spans: Iterable[str],
    *,
    payload: Mapping[str, Any] | None = None,
) -> bool:
    """Compile structural conflict behavior from the contract rule values.

    The canonical rule permits a strictly smaller relation-only basis nested
    inside broader requested-output evidence while rejecting equality and a basis
    that wraps the requested output.  The semantic verifier still decides whether
    a literal basis is actually relation-only; this helper owns containment only.

    ``payload`` exists for deterministic mutation/property proofs.  Production
    code omits it and cannot select alternate semantics.
    """

    basis = str(basis_span or "").strip()
    if not basis:
        return False

    contract = _contract_view(payload)
    rules = _section(contract, "rules")
    equality_forbidden = rules.get("requested_output_equality") == "forbidden"
    wrapper_forbidden = rules.get("requested_output_wrapper") == "forbidden"
    nested_forbidden = rules.get("strict_nested_requested_output") != "allowed"

    for raw in requested_output_spans:
        output = str(raw or "").strip()
        if not output:
            continue
        if basis == output:
            if equality_forbidden:
                return True
            continue
        if output in basis and wrapper_forbidden:
            return True
        if basis in output and nested_forbidden:
            return True
    return False


def _projection_provenance(payload: Mapping[str, Any]) -> str:
    contract_id = str(payload.get("contract_id") or "")
    return f"[contract={contract_id} sha256={contract_fingerprint(payload)}]"


def _kind_phrase(raw: object) -> str:
    value = str(raw or "").strip()
    return value.replace("_", "-")


def _compiled_semantic_rule(payload: Mapping[str, Any]) -> str:
    """Compile the shared semantic clause used by every model projection."""

    basis = _section(payload, "basis")
    rules = _section(payload, "rules")
    kinds = [
        _kind_phrase(item)
        for item in basis.get("allowed_kinds", [])
        if str(item or "").strip()
    ]
    kind_text = ", ".join(kinds) if kinds else "no dependency relation kinds"

    clauses = [
        f"dependency basis evidence may identify only these relation kinds: {kind_text}."
    ]
    if basis.get("relation_only_required") is True:
        clauses.append("The basis itself must be relation-only.")
    else:
        clauses.append("Relation-only evidence is not required by this contract version.")

    equality_rule = str(rules.get("requested_output_equality") or "")
    if equality_rule == "forbidden":
        clauses.append("A basis must not equal a requested-output evidence span.")
    else:
        clauses.append(
            f"Requested-output equality is {equality_rule or 'unspecified'} by this contract."
        )

    wrapper_rule = str(rules.get("requested_output_wrapper") or "")
    if wrapper_rule == "forbidden":
        clauses.append(
            "A basis must not wrap a requested-output evidence span with action/control wording."
        )
    else:
        clauses.append(
            f"Requested-output wrapping is {wrapper_rule or 'unspecified'} by this contract."
        )

    nested_rule = str(rules.get("strict_nested_requested_output") or "")
    if nested_rule == "allowed":
        clauses.append(
            "A strictly smaller relation-only literal basis nested inside a broader "
            "requested-output evidence span is admissible; use the smallest such literal basis."
        )
    else:
        clauses.append(
            "A strictly smaller basis nested inside requested-output evidence is "
            f"{nested_rule or 'unspecified'} by this contract."
        )

    fallback = str(rules.get("no_valid_relation_only_basis") or "").strip()
    if fallback:
        clauses.append(
            "When no valid relation-only basis exists under these rules, return "
            f"relation={fallback}."
        )
    else:
        clauses.append("When no valid relation-only basis exists, fail closed.")
    return " ".join(clauses)


def render_candidate_blind_dependency_rule(
    payload: Mapping[str, Any] | None = None,
) -> str:
    """Render the canonical dependency rule for the blind semantic verifier."""

    contract = _contract_view(payload)
    return f"{_projection_provenance(contract)} {_compiled_semantic_rule(contract)}"


def render_dependency_format_repair_rule(
    payload: Mapping[str, Any] | None = None,
) -> str:
    """Render the same canonical rule for structured-output format repair."""

    contract = _contract_view(payload)
    return (
        f"{_projection_provenance(contract)} FORMAT-REPAIR PROJECTION: "
        f"{_compiled_semantic_rule(contract)}"
    )


def projection_manifest(
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic provenance record for every semantic text projection."""

    contract = _contract_view(payload)
    authority = _section(contract, "authority")
    return {
        "schema": "dependency-basis-projection-manifest@2",
        "contract_id": str(contract.get("contract_id") or ""),
        "contract_sha256": contract_fingerprint(contract),
        "contract_version": contract.get("version"),
        "final_dependency_authority": authority.get("final_dependency_authority"),
        "authority_effect": authority.get("authority_effect"),
        "basis": deepcopy(dict(_section(contract, "basis"))),
        "rules": deepcopy(dict(_section(contract, "rules"))),
        "projections": {
            "candidate_blind_dependency_rule": render_candidate_blind_dependency_rule(contract),
            "format_repair_dependency_rule": render_dependency_format_repair_rule(contract),
        },
    }


def verify_projection_manifest(payload: Mapping[str, Any]) -> bool:
    return dict(payload) == projection_manifest()


def mutation_detection_matrix() -> dict[str, bool]:
    """Prove contract mutations change generated behavior/projections or lose authority safety."""

    results: dict[str, bool] = {}
    baseline = canonical_contract()
    baseline_blind = render_candidate_blind_dependency_rule(baseline)
    baseline_repair = render_dependency_format_repair_rule(baseline)

    nested = canonical_contract()
    nested["rules"]["strict_nested_requested_output"] = "forbidden"
    results["nested_rule_flipped"] = (
        dependency_basis_conflicts_with_requested_outputs(
            "它", ["它能不能退款"], payload=baseline
        )
        is False
        and dependency_basis_conflicts_with_requested_outputs(
            "它", ["它能不能退款"], payload=nested
        )
        is True
        and render_candidate_blind_dependency_rule(nested) != baseline_blind
    )

    equality = canonical_contract()
    equality["rules"]["requested_output_equality"] = "allowed"
    results["equality_rule_flipped"] = (
        dependency_basis_conflicts_with_requested_outputs(
            "它能不能退款", ["它能不能退款"], payload=baseline
        )
        is True
        and dependency_basis_conflicts_with_requested_outputs(
            "它能不能退款", ["它能不能退款"], payload=equality
        )
        is False
        and render_candidate_blind_dependency_rule(equality) != baseline_blind
    )

    wrapper = canonical_contract()
    wrapper["rules"]["requested_output_wrapper"] = "allowed"
    results["wrapper_rule_flipped"] = (
        dependency_basis_conflicts_with_requested_outputs(
            "看看它能不能退款", ["退款"], payload=baseline
        )
        is True
        and dependency_basis_conflicts_with_requested_outputs(
            "看看它能不能退款", ["退款"], payload=wrapper
        )
        is False
        and render_candidate_blind_dependency_rule(wrapper) != baseline_blind
    )

    fallback = canonical_contract()
    fallback["rules"]["no_valid_relation_only_basis"] = "dependent"
    results["independence_fallback_flipped"] = (
        render_candidate_blind_dependency_rule(fallback) != baseline_blind
        and render_dependency_format_repair_rule(fallback) != baseline_repair
    )

    relation_only = canonical_contract()
    relation_only["basis"]["relation_only_required"] = False
    results["relation_only_disabled"] = (
        render_candidate_blind_dependency_rule(relation_only) != baseline_blind
        and "relation_only_requirement_drift" in validate_contract(relation_only)
    )

    authority = canonical_contract()
    authority["authority"]["final_dependency_authority"] = "model_goal_alignment_verifier"
    results["authority_changed"] = (
        "final_dependency_authority_drift" in validate_contract(authority)
    )

    authority_effect = canonical_contract()
    authority_effect["authority"]["authority_effect"] = True
    results["authority_effect_enabled"] = (
        "canonical_contract_gained_authority" in validate_contract(authority_effect)
    )

    manifest = projection_manifest()
    drifted_text = deepcopy(manifest)
    drifted_text["projections"]["candidate_blind_dependency_rule"] += " DRIFT"
    results["projection_text_drift"] = not verify_projection_manifest(drifted_text)

    drifted_hash = deepcopy(manifest)
    drifted_hash["contract_sha256"] = "0" * 64
    results["projection_hash_drift"] = not verify_projection_manifest(drifted_hash)

    return results
