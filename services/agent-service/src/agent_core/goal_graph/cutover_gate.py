from __future__ import annotations

"""Inert cutover-readiness and rollback contracts for dependency authority.

Stage 4B is deliberately non-activating. It can prove that a sealed dependency
attestation and an externally supplied governance grant match the current
identity, but it never changes the runtime dependency authority. Any future
activation must happen at a separate, explicit authority boundary.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from .dependency_authority import dependency_authority_attestation_integrity

DEPENDENCY_CUTOVER_GATE_VERSION = "typed-dependency-cutover-gate@1"
DEPENDENCY_CUTOVER_GRANT_VERSION = "typed-dependency-cutover-grant@1"
DEPENDENCY_CUTOVER_GRANT_AUTHORITY = "external_governance_grant"
DEPENDENCY_ROLLBACK_CONTRACT_VERSION = "typed-dependency-rollback-contract@1"

LEGACY_DEPENDENCY_AUTHORITY = "legacy_declared_goal_dependencies"
TYPED_DEPENDENCY_AUTHORITY = "verified_dataflow_edges_only"

_IDENTITY_FIELDS = (
    "semantic_contract_id",
    "semantic_digest",
    "typed_graph_id",
    "typed_graph_digest",
    "typed_coverage_digest",
    "capability_registry_version",
    "completion_snapshot_digest",
)


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _sealed_digest_errors(
    row: dict[str, Any],
    *,
    digest_field: str,
    missing_code: str,
    invalid_code: str,
) -> list[str]:
    stored = _text(row.get(digest_field), limit=128)
    if not stored:
        return [missing_code]
    payload = deepcopy(row)
    payload.pop(digest_field, None)
    return [] if stored == _digest(payload) else [invalid_code]


def dependency_cutover_grant_integrity(grant: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a grant record as data, never as runtime authority."""

    row = deepcopy(grant) if isinstance(grant, dict) else {}
    errors: list[str] = []

    if row.get("version") != DEPENDENCY_CUTOVER_GRANT_VERSION:
        errors.append("CUTOVER_GRANT_VERSION_INVALID")
    if row.get("authority") != DEPENDENCY_CUTOVER_GRANT_AUTHORITY:
        errors.append("CUTOVER_GRANT_AUTHORITY_INVALID")
    if row.get("immutable") is not True:
        errors.append("CUTOVER_GRANT_IMMUTABLE_REQUIRED")
    if _text(row.get("status"), limit=80) != "GRANTED":
        errors.append("CUTOVER_GRANT_STATUS_INVALID")
    if row.get("external_authority_verified") is not True:
        errors.append("CUTOVER_GRANT_EXTERNAL_AUTHORITY_REQUIRED")
    if not _text(row.get("grant_id"), limit=500):
        errors.append("CUTOVER_GRANT_ID_REQUIRED")
    if not _text(row.get("issued_by"), limit=500):
        errors.append("CUTOVER_GRANT_ISSUER_REQUIRED")
    if not _text(row.get("attestation_digest"), limit=128):
        errors.append("CUTOVER_GRANT_ATTESTATION_DIGEST_REQUIRED")

    for field in _IDENTITY_FIELDS:
        if not _text(row.get(field), limit=500):
            errors.append(f"CUTOVER_GRANT_{field.upper()}_REQUIRED")

    try:
        expires_at = float(row.get("expires_at"))
    except (TypeError, ValueError):
        expires_at = 0.0
    if expires_at <= 0:
        errors.append("CUTOVER_GRANT_EXPIRY_REQUIRED")

    errors.extend(
        _sealed_digest_errors(
            row,
            digest_field="grant_digest",
            missing_code="CUTOVER_GRANT_DIGEST_REQUIRED",
            invalid_code="CUTOVER_GRANT_DIGEST_INVALID",
        )
    )
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "grant_digest": _text(row.get("grant_digest"), limit=128) or None,
    }


def _current_identity_errors(
    *,
    attestation: dict[str, Any],
    current_identity: dict[str, Any] | None,
) -> list[str]:
    current = current_identity if isinstance(current_identity, dict) else {}
    errors: list[str] = []
    for field in _IDENTITY_FIELDS:
        actual = _text(current.get(field), limit=500)
        expected = _text(attestation.get(field), limit=500)
        if not actual:
            errors.append(f"CURRENT_{field.upper()}_REQUIRED")
        elif actual != expected:
            errors.append(f"CURRENT_{field.upper()}_MISMATCH")
    return errors


def _grant_binding_errors(
    *,
    attestation: dict[str, Any],
    grant: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if _text(grant.get("attestation_digest"), limit=128) != _text(
        attestation.get("attestation_digest"), limit=128
    ):
        errors.append("CUTOVER_GRANT_ATTESTATION_MISMATCH")
    for field in _IDENTITY_FIELDS:
        if _text(grant.get(field), limit=500) != _text(attestation.get(field), limit=500):
            errors.append(f"CUTOVER_GRANT_{field.upper()}_MISMATCH")
    return errors


def evaluate_dependency_cutover_gate(
    *,
    attestation: dict[str, Any] | None,
    grant: dict[str, Any] | None,
    current_identity: dict[str, Any] | None,
    evaluation_time: float | None,
) -> dict[str, Any]:
    """Evaluate readiness while keeping legacy authority selected."""

    sealed_attestation = deepcopy(attestation) if isinstance(attestation, dict) else {}
    sealed_grant = deepcopy(grant) if isinstance(grant, dict) else {}
    errors: list[str] = []

    attestation_integrity = dependency_authority_attestation_integrity(sealed_attestation)
    if not attestation_integrity.get("ok"):
        errors.extend(
            f"ATTESTATION:{code}"
            for code in list(attestation_integrity.get("errors") or [])
        )
    if _text(sealed_attestation.get("eligibility_status"), limit=120) != "ELIGIBLE_EVIDENCE_ONLY":
        errors.append("ATTESTATION_NOT_ELIGIBLE_EVIDENCE_ONLY")

    errors.extend(
        _current_identity_errors(
            attestation=sealed_attestation,
            current_identity=current_identity,
        )
    )

    grant_integrity = dependency_cutover_grant_integrity(sealed_grant)
    if not grant_integrity.get("ok"):
        errors.extend(
            f"GRANT:{code}"
            for code in list(grant_integrity.get("errors") or [])
        )
    if sealed_grant:
        errors.extend(
            _grant_binding_errors(
                attestation=sealed_attestation,
                grant=sealed_grant,
            )
        )

    if evaluation_time is None:
        errors.append("CUTOVER_EVALUATION_TIME_REQUIRED")
    else:
        try:
            now = float(evaluation_time)
        except (TypeError, ValueError):
            errors.append("CUTOVER_EVALUATION_TIME_INVALID")
        else:
            try:
                expires_at = float(sealed_grant.get("expires_at"))
            except (TypeError, ValueError):
                expires_at = 0.0
            if expires_at > 0 and now >= expires_at:
                errors.append("CUTOVER_GRANT_EXPIRED")

    unique_errors = sorted(set(errors))
    candidate_ready = not unique_errors
    payload: dict[str, Any] = {
        "version": DEPENDENCY_CUTOVER_GATE_VERSION,
        "authority": "read_only_cutover_readiness_not_runtime_authority",
        "status": "CANDIDATE_READY" if candidate_ready else "BLOCKED",
        "attestation_digest": _text(
            sealed_attestation.get("attestation_digest"), limit=128
        ) or None,
        "grant_digest": _text(sealed_grant.get("grant_digest"), limit=128) or None,
        "current_identity": {
            field: _text((current_identity or {}).get(field), limit=500) or None
            for field in _IDENTITY_FIELDS
        },
        "current_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "candidate_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,
        "selected_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "cutover_candidate_ready": candidate_ready,
        "grant_shape_and_binding_accepted": bool(
            candidate_ready and grant_integrity.get("ok")
        ),
        "runtime_activation_authority_granted": False,
        "cutover_performed": False,
        "single_authority_invariant": True,
        "changes_current_dependency_blocking": False,
        "changes_allowed_capability_tools": False,
        "blocks_execution": False,
        "creates_permit": False,
        "mutates_semantics": False,
        "mutates_business_state": False,
        "errors": unique_errors,
    }
    payload["gate_digest"] = _digest(payload)
    return payload


def dependency_cutover_gate_integrity(gate: dict[str, Any] | None) -> dict[str, Any]:
    row = deepcopy(gate) if isinstance(gate, dict) else {}
    errors: list[str] = []

    if row.get("version") != DEPENDENCY_CUTOVER_GATE_VERSION:
        errors.append("CUTOVER_GATE_VERSION_INVALID")
    if row.get("authority") != "read_only_cutover_readiness_not_runtime_authority":
        errors.append("CUTOVER_GATE_AUTHORITY_INVALID")
    errors.extend(
        _sealed_digest_errors(
            row,
            digest_field="gate_digest",
            missing_code="CUTOVER_GATE_DIGEST_REQUIRED",
            invalid_code="CUTOVER_GATE_DIGEST_INVALID",
        )
    )
    if row.get("selected_runtime_dependency_authority") != LEGACY_DEPENDENCY_AUTHORITY:
        errors.append("CUTOVER_GATE_MUST_KEEP_LEGACY_SELECTED")
    if row.get("current_runtime_dependency_authority") != LEGACY_DEPENDENCY_AUTHORITY:
        errors.append("CUTOVER_GATE_CURRENT_AUTHORITY_INVALID")
    if row.get("candidate_dependency_authority") != TYPED_DEPENDENCY_AUTHORITY:
        errors.append("CUTOVER_GATE_CANDIDATE_AUTHORITY_INVALID")
    for field in (
        "runtime_activation_authority_granted",
        "cutover_performed",
        "changes_current_dependency_blocking",
        "changes_allowed_capability_tools",
        "blocks_execution",
        "creates_permit",
        "mutates_semantics",
        "mutates_business_state",
    ):
        if bool(row.get(field)):
            errors.append(f"{field.upper()}_MUST_BE_FALSE")
    if row.get("single_authority_invariant") is not True:
        errors.append("CUTOVER_GATE_SINGLE_AUTHORITY_REQUIRED")

    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "gate_digest": _text(row.get("gate_digest"), limit=128) or None,
    }


def build_dependency_authority_rollback_contract(
    *,
    gate: dict[str, Any] | None,
    rollback_requested: bool = False,
    reason_code: str | None = None,
) -> dict[str, Any]:
    """Build an inert reversion contract whose only target is legacy authority."""

    sealed_gate = deepcopy(gate) if isinstance(gate, dict) else {}
    integrity = dependency_cutover_gate_integrity(sealed_gate)
    reasons: list[str] = []
    if rollback_requested:
        reasons.append(_text(reason_code, limit=200) or "EXPLICIT_ROLLBACK_REQUEST")
    if not integrity.get("ok"):
        reasons.extend(
            f"CUTOVER_GATE:{code}" for code in list(integrity.get("errors") or [])
        )
    elif sealed_gate.get("status") != "CANDIDATE_READY":
        reasons.append("CUTOVER_GATE_NOT_READY")

    payload: dict[str, Any] = {
        "version": DEPENDENCY_ROLLBACK_CONTRACT_VERSION,
        "authority": "read_only_reversion_contract_not_runtime_authority",
        "source_gate_digest": _text(sealed_gate.get("gate_digest"), limit=128) or None,
        "current_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "selected_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "reversion_target": LEGACY_DEPENDENCY_AUTHORITY,
        "rollback_policy": "revert_to_legacy_on_gate_failure_identity_drift_expiry_or_explicit_request",
        "would_revert_if_typed_active": bool(reasons),
        "runtime_reversion_required_now": False,
        "reversion_performed": False,
        "mutates_runtime_authority": False,
        "changes_allowed_capability_tools": False,
        "creates_permit": False,
        "mutates_semantics": False,
        "mutates_business_state": False,
        "reason_codes": sorted(set(reasons)),
    }
    payload["rollback_digest"] = _digest(payload)
    return payload


def dependency_authority_rollback_integrity(
    rollback: dict[str, Any] | None,
) -> dict[str, Any]:
    row = deepcopy(rollback) if isinstance(rollback, dict) else {}
    errors: list[str] = []
    if row.get("version") != DEPENDENCY_ROLLBACK_CONTRACT_VERSION:
        errors.append("ROLLBACK_VERSION_INVALID")
    if row.get("authority") != "read_only_reversion_contract_not_runtime_authority":
        errors.append("ROLLBACK_AUTHORITY_INVALID")
    errors.extend(
        _sealed_digest_errors(
            row,
            digest_field="rollback_digest",
            missing_code="ROLLBACK_DIGEST_REQUIRED",
            invalid_code="ROLLBACK_DIGEST_INVALID",
        )
    )
    for field in (
        "current_runtime_dependency_authority",
        "selected_runtime_dependency_authority",
        "reversion_target",
    ):
        if row.get(field) != LEGACY_DEPENDENCY_AUTHORITY:
            errors.append(f"{field.upper()}_MUST_BE_LEGACY")
    for field in (
        "runtime_reversion_required_now",
        "reversion_performed",
        "mutates_runtime_authority",
        "changes_allowed_capability_tools",
        "creates_permit",
        "mutates_semantics",
        "mutates_business_state",
    ):
        if bool(row.get(field)):
            errors.append(f"{field.upper()}_MUST_BE_FALSE")
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "rollback_digest": _text(row.get("rollback_digest"), limit=128) or None,
    }


__all__ = [
    "DEPENDENCY_CUTOVER_GATE_VERSION",
    "DEPENDENCY_CUTOVER_GRANT_AUTHORITY",
    "DEPENDENCY_CUTOVER_GRANT_VERSION",
    "DEPENDENCY_ROLLBACK_CONTRACT_VERSION",
    "LEGACY_DEPENDENCY_AUTHORITY",
    "TYPED_DEPENDENCY_AUTHORITY",
    "build_dependency_authority_rollback_contract",
    "dependency_authority_rollback_integrity",
    "dependency_cutover_gate_integrity",
    "dependency_cutover_grant_integrity",
    "evaluate_dependency_cutover_gate",
]
