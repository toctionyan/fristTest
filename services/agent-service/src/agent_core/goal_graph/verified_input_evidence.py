from __future__ import annotations

"""Strict, non-executable input evidence for Typed Goal Graph Stage 2A."""

from copy import deepcopy
from hashlib import sha256
from math import isfinite
import re
from typing import Any, Callable

from .contracts import canonical_digest, normalize_cardinality, normalize_scope

VERIFIED_INPUT_EVIDENCE_VERSION = "verified-input-evidence@2"
_KEYS = frozenset(
    {
        "version",
        "source_type",
        "type_name",
        "resource_type",
        "cardinality",
        "authority",
        "scope",
        "semantic_contract_id",
        "semantic_digest",
        "issuer",
        "issuer_attestation",
        "issued_at",
        "expires_at",
        "evaluated_at",
        "proof_ref",
        "proof_digest",
    }
)
_POINTER_RE = re.compile(
    r"^(?:artifact|context|evidence|input|proof|result|trace|view):"
    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,479}$"
)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
IssuerValidator = Callable[[dict[str, Any]], bool]


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def verified_input_evidence_proof_payload(evidence: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(evidence)
    payload.pop("proof_digest", None)
    return payload


def validate_verified_input_evidence(
    evidence: Any,
    *,
    expected_scope: dict[str, Any],
    expected_type_name: str | None = None,
    expected_resource_type: str | None = None,
    expected_cardinality: str | None = None,
    expected_authority: str | None = None,
    expected_semantic_contract_id: str | None = None,
    expected_semantic_digest: str | None = None,
    evaluation_time: float | None = None,
    max_age_seconds: float | None = None,
    issuer_validator: IssuerValidator | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(evidence, dict):
        return {
            "ok": False,
            "status": "REJECTED",
            "readiness": "DIAGNOSTIC_ONLY",
            "trusted_issuer": False,
            "proof_digest_valid": False,
            "errors": ["VERIFIED_INPUT_EVIDENCE_MUST_BE_OBJECT"],
        }

    errors.extend(f"VERIFIED_INPUT_EVIDENCE_UNKNOWN_FIELD:{key}" for key in sorted(set(evidence) - _KEYS))
    errors.extend(f"VERIFIED_INPUT_EVIDENCE_FIELD_REQUIRED:{key}" for key in sorted(_KEYS - set(evidence)))
    if evidence.get("version") != VERIFIED_INPUT_EVIDENCE_VERSION:
        errors.append("VERIFIED_INPUT_EVIDENCE_VERSION_INVALID")

    source_type = _text(evidence.get("source_type"), limit=120)
    type_name = _text(evidence.get("type_name"), limit=240)
    resource_type = _text(evidence.get("resource_type"), limit=200).casefold()
    authority = _text(evidence.get("authority"), limit=200)
    if not source_type:
        errors.append("VERIFIED_INPUT_EVIDENCE_SOURCE_TYPE_REQUIRED")
    if not type_name:
        errors.append("VERIFIED_INPUT_EVIDENCE_TYPE_NAME_REQUIRED")
    if not resource_type:
        errors.append("VERIFIED_INPUT_EVIDENCE_RESOURCE_TYPE_REQUIRED")
    if not authority:
        errors.append("VERIFIED_INPUT_EVIDENCE_AUTHORITY_REQUIRED")
    if normalize_scope(evidence.get("scope") if isinstance(evidence.get("scope"), dict) else {}) != normalize_scope(expected_scope):
        errors.append("VERIFIED_INPUT_EVIDENCE_SCOPE_MISMATCH")
    if expected_type_name and type_name != _text(expected_type_name, limit=240):
        errors.append("VERIFIED_INPUT_EVIDENCE_TYPE_MISMATCH")
    if expected_resource_type and resource_type != _text(expected_resource_type, limit=200).casefold():
        errors.append("VERIFIED_INPUT_EVIDENCE_RESOURCE_TYPE_MISMATCH")
    if expected_cardinality and normalize_cardinality(evidence.get("cardinality")) != normalize_cardinality(expected_cardinality):
        errors.append("VERIFIED_INPUT_EVIDENCE_CARDINALITY_MISMATCH")
    if expected_authority and authority != _text(expected_authority, limit=200):
        errors.append("VERIFIED_INPUT_EVIDENCE_AUTHORITY_MISMATCH")
    if expected_semantic_contract_id and _text(evidence.get("semantic_contract_id"), limit=500) != _text(expected_semantic_contract_id, limit=500):
        errors.append("VERIFIED_INPUT_EVIDENCE_SEMANTIC_CONTRACT_MISMATCH")
    if expected_semantic_digest and _text(evidence.get("semantic_digest"), limit=128) != _text(expected_semantic_digest, limit=128):
        errors.append("VERIFIED_INPUT_EVIDENCE_SEMANTIC_DIGEST_MISMATCH")
    if not _POINTER_RE.fullmatch(_text(evidence.get("proof_ref"), limit=500)):
        errors.append("VERIFIED_INPUT_EVIDENCE_PROOF_REF_INVALID")
    if not _POINTER_RE.fullmatch(_text(evidence.get("issuer_attestation"), limit=500)):
        errors.append("VERIFIED_INPUT_EVIDENCE_ATTESTATION_INVALID")
    if not _text(evidence.get("issuer"), limit=240):
        errors.append("VERIFIED_INPUT_EVIDENCE_ISSUER_REQUIRED")

    issued_at = _number(evidence.get("issued_at"))
    expires_at = _number(evidence.get("expires_at"))
    evaluated_at = _number(evidence.get("evaluated_at"))
    if issued_at is None:
        errors.append("VERIFIED_INPUT_EVIDENCE_ISSUED_AT_INVALID")
    if expires_at is None or expires_at <= 0:
        errors.append("VERIFIED_INPUT_EVIDENCE_EXPIRES_AT_INVALID")
    if evaluated_at is None:
        errors.append("VERIFIED_INPUT_EVIDENCE_EVALUATED_AT_INVALID")
    if issued_at is not None and expires_at is not None and issued_at >= expires_at:
        errors.append("VERIFIED_INPUT_EVIDENCE_TIME_WINDOW_INVALID")
    if issued_at is not None and evaluated_at is not None and issued_at > evaluated_at:
        errors.append("VERIFIED_INPUT_EVIDENCE_EVALUATED_BEFORE_ISSUED")
    if evaluated_at is not None and expires_at is not None and evaluated_at >= expires_at:
        errors.append("VERIFIED_INPUT_EVIDENCE_EVALUATED_AFTER_EXPIRY")
    if evaluation_time is not None and evaluated_at is not None and float(evaluation_time) != evaluated_at:
        errors.append("VERIFIED_INPUT_EVIDENCE_EVALUATION_TIME_MISMATCH")
    if max_age_seconds is not None:
        max_age = _number(max_age_seconds)
        if max_age is None or max_age <= 0:
            errors.append("VERIFIED_INPUT_EVIDENCE_MAX_AGE_INVALID")
        elif issued_at is not None and evaluated_at is not None and evaluated_at - issued_at > max_age:
            errors.append("VERIFIED_INPUT_EVIDENCE_MAX_AGE_EXCEEDED")
    if evaluation_time is not None and expires_at is not None and float(evaluation_time) >= expires_at:
        errors.append("VERIFIED_INPUT_EVIDENCE_EXPIRED")

    expected_digest = _text(evidence.get("proof_digest"), limit=128)
    digest_valid = bool(_HEX64_RE.fullmatch(expected_digest)) and canonical_digest(
        verified_input_evidence_proof_payload(evidence)
    ) == expected_digest
    if not digest_valid:
        errors.append("VERIFIED_INPUT_EVIDENCE_PROOF_DIGEST_INVALID")

    trusted = False
    if issuer_validator is None:
        errors.append("VERIFIED_INPUT_EVIDENCE_TRUSTED_ISSUER_UNAVAILABLE")
    elif not errors:
        try:
            trusted = bool(issuer_validator(deepcopy(evidence)))
        except Exception:
            trusted = False
        if not trusted:
            errors.append("VERIFIED_INPUT_EVIDENCE_ISSUER_REJECTED")
    return {
        "ok": not errors and trusted,
        "status": "READY" if not errors and trusted else "REJECTED",
        "readiness": "READY" if not errors and trusted else "DIAGNOSTIC_ONLY",
        "trusted_issuer": trusted,
        "proof_digest_valid": digest_valid,
        "version": evidence.get("version"),
        "errors": sorted(set(errors)),
    }


__all__ = [
    "VERIFIED_INPUT_EVIDENCE_VERSION",
    "verified_input_evidence_proof_payload",
    "validate_verified_input_evidence",
]
