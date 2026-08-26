from __future__ import annotations

"""Strict, non-executable target evidence for Typed Goal Graph Stage 2A.

This module is intentionally a boundary contract, not an issuer.  It accepts
evidence envelopes produced by a runtime-owned authority and only reports
whether the envelope is admissible for shadow diagnostics.  Recomputing a
digest, or filling in ``verified`` fields in a frozen graph, is never enough to
produce ``READY`` evidence.
"""

from copy import deepcopy
from math import isfinite
import re
from typing import Any, Callable, Iterable

from .contracts import canonical_digest, normalize_scope

TARGET_EVIDENCE_VERSION = "typed-target-evidence@2"
LEGACY_TARGET_EVIDENCE_VERSION = "typed-target-binding@1"
TARGET_EVIDENCE_VARIANTS = frozenset(
    {
        "historical_visible_result",
        "deterministic_target_resolver_projection",
        "same_turn_verified_capability_output",
    }
)

_COMMON_KEYS = frozenset(
    {
        "version",
        "variant",
        "resource_type",
        "logical_type_name",
        "cardinality",
        "scope",
        "semantic_contract_id",
        "semantic_digest",
        "authority",
        "issuer",
        "issuer_attestation",
        "issued_at",
        "expires_at",
        "evaluated_at",
        "proof_ref",
        "proof_digest",
        "source",
    }
)
_SOURCE_KEYS = {
    "historical_visible_result": frozenset(
        {"result_ref", "member_handles", "reference_kind"}
    ),
    "deterministic_target_resolver_projection": frozenset(
        {"source_evidence_ref", "projection", "projection_digest"}
    ),
    "same_turn_verified_capability_output": frozenset(
        {
            "goal_output_ref",
            "artifact_handle",
            "execution_trace_ref",
            "permit_ref",
            "match_proof_ref",
            "producer_goal_id",
            "producer_output_id",
        }
    ),
}
_EXPECTED_AUTHORITIES = {
    "historical_visible_result": "runtime_visible_result_ref",
    "deterministic_target_resolver_projection": "runtime_target_resolver_projection",
    "same_turn_verified_capability_output": "runtime_capability_output_issuer",
}
_POINTER_RE = re.compile(
    r"^(?:artifact|goal-output|match-proof|permit|proof|projection|result|trace|view):"
    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,479}$"
)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_NON_EMPTY_SCOPE_KEYS = frozenset({"tenant_id", "user_id", "thread_id"})


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _pointer(value: Any) -> bool:
    return bool(_POINTER_RE.fullmatch(_text(value, limit=500)))


def _digest(value: Any) -> bool:
    return bool(_HEX64_RE.fullmatch(_text(value, limit=128)))


def _failure(code: str, *, path: str | None = None) -> dict[str, str]:
    row = {"code": code}
    if path:
        row["path"] = path
    return row


def _source_shape_errors(source: Any, *, variant: str) -> list[dict[str, str]]:
    if not isinstance(source, dict):
        return [_failure("TARGET_EVIDENCE_SOURCE_MUST_BE_OBJECT", path="source")]
    expected = _SOURCE_KEYS[variant]
    unknown = sorted(set(source) - expected)
    missing = sorted(expected - set(source))
    errors = [
        _failure("TARGET_EVIDENCE_UNKNOWN_SOURCE_FIELD", path=f"source.{key}")
        for key in unknown
    ]
    errors.extend(
        _failure("TARGET_EVIDENCE_SOURCE_FIELD_REQUIRED", path=f"source.{key}")
        for key in missing
    )
    return errors


def _source_value_errors(
    source: dict[str, Any], *, variant: str, cardinality: str
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if variant == "historical_visible_result":
        if not _pointer(source.get("result_ref")):
            errors.append(_failure("TARGET_EVIDENCE_RESULT_REF_INVALID", path="source.result_ref"))
        members = source.get("member_handles")
        if not isinstance(members, list) or not members or any(not _pointer(item) for item in members):
            errors.append(_failure("TARGET_EVIDENCE_MEMBER_HANDLES_INVALID", path="source.member_handles"))
        elif cardinality == "exactly_one" and len(members) != 1:
            errors.append(_failure("TARGET_EVIDENCE_CARDINALITY_MEMBER_COUNT_MISMATCH", path="source.member_handles"))
        reference_kind = _text(source.get("reference_kind"), limit=100)
        if reference_kind != "customer_visible":
            errors.append(_failure("TARGET_EVIDENCE_REFERENCE_NOT_CUSTOMER_VISIBLE", path="source.reference_kind"))
    elif variant == "deterministic_target_resolver_projection":
        if not _pointer(source.get("source_evidence_ref")):
            errors.append(_failure("TARGET_EVIDENCE_SOURCE_EVIDENCE_REF_INVALID", path="source.source_evidence_ref"))
        if not _digest(source.get("projection_digest")):
            errors.append(_failure("TARGET_EVIDENCE_PROJECTION_DIGEST_INVALID", path="source.projection_digest"))
        projection = source.get("projection")
        expected_projection_keys = {"kind", "member_handle"} if cardinality == "exactly_one" else {"kind", "member_handles"}
        if not isinstance(projection, dict):
            errors.append(_failure("TARGET_EVIDENCE_PROJECTION_MUST_BE_OBJECT", path="source.projection"))
        else:
            unknown = sorted(set(projection) - expected_projection_keys)
            missing = sorted(expected_projection_keys - set(projection))
            errors.extend(
                _failure("TARGET_EVIDENCE_UNKNOWN_PROJECTION_FIELD", path=f"source.projection.{key}")
                for key in unknown
            )
            errors.extend(
                _failure("TARGET_EVIDENCE_PROJECTION_FIELD_REQUIRED", path=f"source.projection.{key}")
                for key in missing
            )
            if _text(projection.get("kind"), limit=100) != "verified_member_selection":
                errors.append(_failure("TARGET_EVIDENCE_PROJECTION_KIND_INVALID", path="source.projection.kind"))
            if cardinality == "exactly_one":
                if not _pointer(projection.get("member_handle")):
                    errors.append(_failure("TARGET_EVIDENCE_MEMBER_HANDLE_INVALID", path="source.projection.member_handle"))
            else:
                members = projection.get("member_handles")
                if not isinstance(members, list) or not members or any(not _pointer(item) for item in members):
                    errors.append(_failure("TARGET_EVIDENCE_MEMBER_HANDLES_INVALID", path="source.projection.member_handles"))
            if _digest(source.get("projection_digest")) and canonical_digest(projection) != source["projection_digest"]:
                errors.append(_failure("TARGET_EVIDENCE_PROJECTION_DIGEST_MISMATCH", path="source.projection_digest"))
    else:
        for key in (
            "goal_output_ref",
            "artifact_handle",
            "execution_trace_ref",
            "permit_ref",
            "match_proof_ref",
        ):
            if not _pointer(source.get(key)):
                errors.append(_failure("TARGET_EVIDENCE_RUNTIME_POINTER_INVALID", path=f"source.{key}"))
        if not _text(source.get("producer_goal_id"), limit=240):
            errors.append(_failure("TARGET_EVIDENCE_PRODUCER_GOAL_REQUIRED", path="source.producer_goal_id"))
        if not _text(source.get("producer_output_id"), limit=240):
            errors.append(_failure("TARGET_EVIDENCE_PRODUCER_OUTPUT_REQUIRED", path="source.producer_output_id"))
    return errors


IssuerValidator = Callable[[dict[str, Any]], bool]


def target_evidence_proof_payload(evidence: dict[str, Any]) -> dict[str, Any]:
    """Return the exact envelope payload an issuer must attest."""

    payload = deepcopy(evidence)
    payload.pop("proof_digest", None)
    return payload


def validate_target_evidence(
    evidence: Any,
    *,
    expected_scope: dict[str, Any],
    expected_resource_type: str | None = None,
    expected_logical_type_name: str | None = None,
    expected_cardinality: str | None = None,
    expected_semantic_contract_id: str | None = None,
    expected_semantic_digest: str | None = None,
    evaluation_time: float | None = None,
    max_age_seconds: float | None = None,
    issuer_validator: IssuerValidator | None = None,
) -> dict[str, Any]:
    """Validate one v2 target envelope without minting trust.

    ``issuer_validator`` represents the runtime-owned root of trust.  Omitting
    it is deliberate and always leaves the evidence diagnostic-only; in
    particular, same-turn capability output can never become ``READY`` from a
    self-signed or recomputed digest.
    """

    errors: list[dict[str, str]] = []
    if not isinstance(evidence, dict):
        return {"ok": False, "status": "REJECTED", "readiness": "DIAGNOSTIC_ONLY", "errors": [_failure("TARGET_EVIDENCE_MUST_BE_OBJECT")]}

    unknown = sorted(set(evidence) - _COMMON_KEYS)
    missing = sorted(_COMMON_KEYS - set(evidence))
    errors.extend(_failure("TARGET_EVIDENCE_UNKNOWN_FIELD", path=key) for key in unknown)
    errors.extend(_failure("TARGET_EVIDENCE_FIELD_REQUIRED", path=key) for key in missing)

    variant = _text(evidence.get("variant"), limit=100)
    version = _text(evidence.get("version"), limit=100)
    if version != TARGET_EVIDENCE_VERSION:
        errors.append(_failure("LEGACY_DIAGNOSTIC_ONLY" if version == LEGACY_TARGET_EVIDENCE_VERSION else "UNSUPPORTED_TARGET_EVIDENCE_VERSION", path="version"))
    if variant not in TARGET_EVIDENCE_VARIANTS:
        errors.append(_failure("TARGET_EVIDENCE_VARIANT_INVALID", path="variant"))

    cardinality = _text(evidence.get("cardinality"), limit=80)
    if cardinality not in {"exactly_one", "collection"}:
        errors.append(_failure("TARGET_EVIDENCE_CARDINALITY_INVALID", path="cardinality"))
    if expected_cardinality == "none":
        errors.append(_failure("TARGET_EVIDENCE_NOT_ALLOWED_FOR_CARDINALITY_NONE", path="cardinality"))
    elif expected_cardinality and cardinality != expected_cardinality:
        errors.append(_failure("TARGET_EVIDENCE_CARDINALITY_MISMATCH", path="cardinality"))

    scope = evidence.get("scope")
    if not isinstance(scope, dict) or set(scope) != _NON_EMPTY_SCOPE_KEYS:
        errors.append(_failure("TARGET_EVIDENCE_SCOPE_INVALID", path="scope"))
    else:
        normalized_scope = normalize_scope(scope)
        if any(not normalized_scope[key] for key in _NON_EMPTY_SCOPE_KEYS):
            errors.append(_failure("TARGET_EVIDENCE_SCOPE_INVALID", path="scope"))
        elif normalized_scope != normalize_scope(expected_scope):
            errors.append(_failure("TARGET_EVIDENCE_SCOPE_MISMATCH", path="scope"))

    for field, expected in (
        ("resource_type", expected_resource_type),
        ("logical_type_name", expected_logical_type_name),
        ("semantic_contract_id", expected_semantic_contract_id),
        ("semantic_digest", expected_semantic_digest),
    ):
        value = _text(evidence.get(field), limit=500)
        if not value:
            errors.append(_failure("TARGET_EVIDENCE_SEMANTIC_IDENTITY_REQUIRED", path=field))
        if expected and value != _text(expected, limit=500):
            errors.append(_failure("TARGET_EVIDENCE_SEMANTIC_IDENTITY_MISMATCH", path=field))
    if not _digest(evidence.get("semantic_digest")):
        errors.append(_failure("TARGET_EVIDENCE_SEMANTIC_DIGEST_INVALID", path="semantic_digest"))

    authority = _text(evidence.get("authority"), limit=120)
    issuer = _text(evidence.get("issuer"), limit=120)
    if variant in _EXPECTED_AUTHORITIES:
        expected_authority = _EXPECTED_AUTHORITIES[variant]
        if authority != expected_authority or issuer != expected_authority:
            errors.append(_failure("TARGET_EVIDENCE_AUTHORITY_INVALID", path="authority"))
    if not _pointer(evidence.get("issuer_attestation")):
        errors.append(_failure("TARGET_EVIDENCE_ISSUER_ATTESTATION_INVALID", path="issuer_attestation"))
    if not _pointer(evidence.get("proof_ref")):
        errors.append(_failure("TARGET_EVIDENCE_PROOF_REF_INVALID", path="proof_ref"))
    if not _digest(evidence.get("proof_digest")):
        errors.append(_failure("TARGET_EVIDENCE_PROOF_DIGEST_INVALID", path="proof_digest"))

    times = {field: _number(evidence.get(field)) for field in ("issued_at", "expires_at", "evaluated_at")}
    if any(value is None for value in times.values()):
        errors.append(_failure("TARGET_EVIDENCE_TIME_INVALID", path="issued_at"))
    else:
        issued = times["issued_at"]
        expires = times["expires_at"]
        evaluated = times["evaluated_at"]
        assert issued is not None and expires is not None and evaluated is not None
        if not issued <= evaluated < expires:
            errors.append(_failure("TARGET_EVIDENCE_TIME_WINDOW_INVALID", path="evaluated_at"))
        if evaluation_time is not None and evaluated != _number(evaluation_time):
            errors.append(_failure("TARGET_EVIDENCE_EVALUATION_TIME_MISMATCH", path="evaluated_at"))
        if max_age_seconds is not None:
            max_age = _number(max_age_seconds)
            if max_age is None or max_age <= 0:
                errors.append(_failure("TARGET_EVIDENCE_MAX_AGE_INVALID", path="issued_at"))
            elif evaluated - issued > max_age:
                errors.append(_failure("TARGET_EVIDENCE_MAX_AGE_EXCEEDED", path="issued_at"))

    source_errors: list[dict[str, str]] = []
    if variant in TARGET_EVIDENCE_VARIANTS:
        source_errors.extend(_source_shape_errors(evidence.get("source"), variant=variant))
        if isinstance(evidence.get("source"), dict) and cardinality in {"exactly_one", "collection"}:
            source_errors.extend(_source_value_errors(evidence["source"], variant=variant, cardinality=cardinality))
    errors.extend(source_errors)

    digest_valid = False
    if _digest(evidence.get("proof_digest")):
        digest_valid = canonical_digest(target_evidence_proof_payload(evidence)) == evidence["proof_digest"]
        if not digest_valid:
            errors.append(_failure("TARGET_EVIDENCE_PROOF_DIGEST_MISMATCH", path="proof_digest"))

    trusted = False
    if issuer_validator is None:
        errors.append(_failure(
            "SAME_TURN_TRUSTED_ISSUER_UNAVAILABLE"
            if variant == "same_turn_verified_capability_output"
            else "TARGET_EVIDENCE_TRUSTED_ISSUER_UNAVAILABLE",
            path="issuer",
        ))
    elif not errors and digest_valid:
        try:
            trusted = bool(issuer_validator(deepcopy(evidence)))
        except Exception:
            trusted = False
        if not trusted:
            errors.append(_failure("TARGET_EVIDENCE_ISSUER_REJECTED", path="issuer"))

    return {
        "ok": not errors and trusted,
        "status": "READY" if not errors and trusted else "REJECTED",
        "readiness": "READY" if not errors and trusted else "DIAGNOSTIC_ONLY",
        "variant": variant or None,
        "version": version or None,
        "proof_digest_valid": digest_valid,
        "trusted_issuer": trusted,
        "errors": errors,
    }


def validate_target_evidence_versions(values: Iterable[Any]) -> dict[str, Any]:
    versions = sorted({_text(value.get("version"), limit=100) for value in values if isinstance(value, dict)})
    if not versions:
        return {"ok": False, "status": "UNSUPPORTED_TARGET_EVIDENCE_VERSION", "versions": []}
    if len(versions) > 1:
        return {"ok": False, "status": "UNSUPPORTED_MIXED_VERSION", "versions": versions}
    if versions[0] == LEGACY_TARGET_EVIDENCE_VERSION:
        return {"ok": False, "status": "LEGACY_DIAGNOSTIC_ONLY", "versions": versions}
    if versions[0] != TARGET_EVIDENCE_VERSION:
        return {"ok": False, "status": "UNSUPPORTED_TARGET_EVIDENCE_VERSION", "versions": versions}
    return {"ok": True, "status": "SUPPORTED", "versions": versions}


__all__ = [
    "LEGACY_TARGET_EVIDENCE_VERSION",
    "TARGET_EVIDENCE_VARIANTS",
    "TARGET_EVIDENCE_VERSION",
    "target_evidence_proof_payload",
    "validate_target_evidence",
    "validate_target_evidence_versions",
]
