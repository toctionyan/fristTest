from __future__ import annotations

"""Stage 4D dependency-authority activation preflight evidence.

This module is deliberately non-activating. It proves that the Stage 4B gate,
Stage 4C rollback drill, an explicit external activation request, and the current
identity all bind to the same candidate. It never changes runtime dependency
authority; a later, separate runtime activation boundary is still required.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from .cutover_gate import (
    LEGACY_DEPENDENCY_AUTHORITY,
    TYPED_DEPENDENCY_AUTHORITY,
    dependency_authority_rollback_integrity,
    dependency_cutover_gate_integrity,
)
from .handoff_simulation import dependency_authority_handoff_simulation_integrity

DEPENDENCY_ACTIVATION_REQUEST_VERSION = "typed-dependency-activation-request@1"
DEPENDENCY_ACTIVATION_REQUEST_AUTHORITY = "external_governance_activation_request"
DEPENDENCY_ACTIVATION_PREFLIGHT_VERSION = "typed-dependency-activation-preflight@1"
DEPENDENCY_ACTIVATION_PREFLIGHT_AUTHORITY = (
    "preflight_only_not_runtime_activation_authority"
)

_IDENTITY_FIELDS = (
    "semantic_contract_id",
    "semantic_digest",
    "typed_graph_id",
    "typed_graph_digest",
    "typed_coverage_digest",
    "capability_registry_version",
    "completion_snapshot_digest",
)

_RUNTIME_FALSE_FIELDS = (
    "runtime_activation_authority_granted",
    "activation_performed",
    "cutover_performed",
    "runtime_reversion_performed",
    "changes_current_dependency_blocking",
    "changes_allowed_capability_tools",
    "blocks_execution",
    "creates_permit",
    "mutates_semantics",
    "mutates_business_state",
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


def dependency_activation_request_integrity(
    request: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate an external request as sealed data, never as activation authority."""

    row = deepcopy(request) if isinstance(request, dict) else {}
    errors: list[str] = []

    if row.get("version") != DEPENDENCY_ACTIVATION_REQUEST_VERSION:
        errors.append("ACTIVATION_REQUEST_VERSION_INVALID")
    if row.get("authority") != DEPENDENCY_ACTIVATION_REQUEST_AUTHORITY:
        errors.append("ACTIVATION_REQUEST_AUTHORITY_INVALID")
    if row.get("immutable") is not True:
        errors.append("ACTIVATION_REQUEST_IMMUTABLE_REQUIRED")
    if _text(row.get("status"), limit=80) != "REQUESTED":
        errors.append("ACTIVATION_REQUEST_STATUS_INVALID")
    if row.get("external_authority_verified") is not True:
        errors.append("ACTIVATION_REQUEST_EXTERNAL_AUTHORITY_REQUIRED")
    if not _text(row.get("request_id"), limit=500):
        errors.append("ACTIVATION_REQUEST_ID_REQUIRED")
    if not _text(row.get("issued_by"), limit=500):
        errors.append("ACTIVATION_REQUEST_ISSUER_REQUIRED")
    if row.get("desired_dependency_authority") != TYPED_DEPENDENCY_AUTHORITY:
        errors.append("ACTIVATION_REQUEST_DESIRED_AUTHORITY_INVALID")
    if (
        row.get("expected_current_runtime_dependency_authority")
        != LEGACY_DEPENDENCY_AUTHORITY
    ):
        errors.append("ACTIVATION_REQUEST_EXPECTED_CURRENT_AUTHORITY_INVALID")

    for field in (
        "attestation_digest",
        "gate_digest",
        "handoff_simulation_digest",
        "rollback_digest",
    ):
        if not _text(row.get(field), limit=128):
            errors.append(f"ACTIVATION_REQUEST_{field.upper()}_REQUIRED")
    for field in _IDENTITY_FIELDS:
        if not _text(row.get(field), limit=500):
            errors.append(f"ACTIVATION_REQUEST_{field.upper()}_REQUIRED")

    try:
        expires_at = float(row.get("expires_at"))
    except (TypeError, ValueError):
        expires_at = 0.0
    if expires_at <= 0:
        errors.append("ACTIVATION_REQUEST_EXPIRY_REQUIRED")

    errors.extend(
        _sealed_digest_errors(
            row,
            digest_field="request_digest",
            missing_code="ACTIVATION_REQUEST_DIGEST_REQUIRED",
            invalid_code="ACTIVATION_REQUEST_DIGEST_INVALID",
        )
    )
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "request_digest": _text(row.get("request_digest"), limit=128) or None,
    }


def _binding_errors(
    *,
    gate: dict[str, Any],
    handoff: dict[str, Any],
    rollback: dict[str, Any],
    request: dict[str, Any],
    current_identity: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    gate_digest = _text(gate.get("gate_digest"), limit=128)
    handoff_digest = _text(handoff.get("simulation_digest"), limit=128)
    rollback_digest = _text(rollback.get("rollback_digest"), limit=128)
    attestation_digest = _text(gate.get("attestation_digest"), limit=128)

    expected = {
        "attestation_digest": attestation_digest,
        "gate_digest": gate_digest,
        "handoff_simulation_digest": handoff_digest,
        "rollback_digest": rollback_digest,
    }
    for field, value in expected.items():
        if _text(request.get(field), limit=128) != value:
            errors.append(f"ACTIVATION_REQUEST_{field.upper()}_MISMATCH")

    if _text(handoff.get("source_gate_digest"), limit=128) != gate_digest:
        errors.append("HANDOFF_SOURCE_GATE_DIGEST_MISMATCH")
    if _text(handoff.get("source_rollback_digest"), limit=128) != rollback_digest:
        errors.append("HANDOFF_SOURCE_ROLLBACK_DIGEST_MISMATCH")
    if _text(rollback.get("source_gate_digest"), limit=128) != gate_digest:
        errors.append("ROLLBACK_SOURCE_GATE_DIGEST_MISMATCH")

    current = current_identity if isinstance(current_identity, dict) else {}
    gate_identity = (
        gate.get("current_identity")
        if isinstance(gate.get("current_identity"), dict)
        else {}
    )
    for field in _IDENTITY_FIELDS:
        actual = _text(current.get(field), limit=500)
        expected_identity = _text(gate_identity.get(field), limit=500)
        if not actual:
            errors.append(f"CURRENT_{field.upper()}_REQUIRED")
        elif actual != expected_identity:
            errors.append(f"CURRENT_{field.upper()}_MISMATCH")
        if _text(request.get(field), limit=500) != expected_identity:
            errors.append(f"ACTIVATION_REQUEST_{field.upper()}_MISMATCH")
    return errors


def evaluate_dependency_activation_preflight(
    *,
    gate: dict[str, Any] | None,
    handoff_simulation: dict[str, Any] | None,
    rollback: dict[str, Any] | None,
    activation_request: dict[str, Any] | None,
    current_identity: dict[str, Any] | None,
    evaluation_time: float | None,
) -> dict[str, Any]:
    """Evaluate Stage 4D readiness while keeping legacy runtime authority selected."""

    sealed_gate = deepcopy(gate) if isinstance(gate, dict) else {}
    sealed_handoff = (
        deepcopy(handoff_simulation) if isinstance(handoff_simulation, dict) else {}
    )
    sealed_rollback = deepcopy(rollback) if isinstance(rollback, dict) else {}
    sealed_request = (
        deepcopy(activation_request) if isinstance(activation_request, dict) else {}
    )
    errors: list[str] = []

    gate_integrity = dependency_cutover_gate_integrity(sealed_gate)
    if not gate_integrity.get("ok"):
        errors.extend(
            f"CUTOVER_GATE:{code}"
            for code in list(gate_integrity.get("errors") or [])
        )
    if sealed_gate.get("status") != "CANDIDATE_READY":
        errors.append("CUTOVER_GATE_NOT_CANDIDATE_READY")
    if sealed_gate.get("cutover_candidate_ready") is not True:
        errors.append("CUTOVER_GATE_CANDIDATE_FLAG_REQUIRED")
    if sealed_gate.get("grant_shape_and_binding_accepted") is not True:
        errors.append("CUTOVER_GATE_GRANT_BINDING_REQUIRED")

    handoff_integrity = dependency_authority_handoff_simulation_integrity(
        sealed_handoff
    )
    if not handoff_integrity.get("ok"):
        errors.extend(
            f"HANDOFF:{code}"
            for code in list(handoff_integrity.get("errors") or [])
        )
    if sealed_handoff.get("status") != "ROLLBACK_DRILL_COMPLETE":
        errors.append("HANDOFF_ROLLBACK_DRILL_REQUIRED")
    if sealed_handoff.get("typed_candidate_entered_in_simulation") is not True:
        errors.append("HANDOFF_TYPED_ENTRY_REQUIRED")
    if sealed_handoff.get("rollback_exercised_in_simulation") is not True:
        errors.append("HANDOFF_ROLLBACK_EXERCISE_REQUIRED")

    rollback_integrity = dependency_authority_rollback_integrity(sealed_rollback)
    if not rollback_integrity.get("ok"):
        errors.extend(
            f"ROLLBACK:{code}"
            for code in list(rollback_integrity.get("errors") or [])
        )
    if sealed_rollback.get("reversion_target") != LEGACY_DEPENDENCY_AUTHORITY:
        errors.append("ROLLBACK_REVERSION_TARGET_INVALID")

    request_integrity = dependency_activation_request_integrity(sealed_request)
    if not request_integrity.get("ok"):
        errors.extend(
            f"REQUEST:{code}"
            for code in list(request_integrity.get("errors") or [])
        )

    errors.extend(
        _binding_errors(
            gate=sealed_gate,
            handoff=sealed_handoff,
            rollback=sealed_rollback,
            request=sealed_request,
            current_identity=current_identity,
        )
    )

    if evaluation_time is None:
        errors.append("ACTIVATION_PREFLIGHT_EVALUATION_TIME_REQUIRED")
    else:
        try:
            now = float(evaluation_time)
        except (TypeError, ValueError):
            errors.append("ACTIVATION_PREFLIGHT_EVALUATION_TIME_INVALID")
        else:
            try:
                expires_at = float(sealed_request.get("expires_at"))
            except (TypeError, ValueError):
                expires_at = 0.0
            if expires_at > 0 and now >= expires_at:
                errors.append("ACTIVATION_REQUEST_EXPIRED")

    unique_errors = sorted(set(errors))
    ready = not unique_errors
    payload: dict[str, Any] = {
        "version": DEPENDENCY_ACTIVATION_PREFLIGHT_VERSION,
        "authority": DEPENDENCY_ACTIVATION_PREFLIGHT_AUTHORITY,
        "status": "ACTIVATION_PREFLIGHT_READY" if ready else "BLOCKED",
        "source_attestation_digest": (
            _text(sealed_gate.get("attestation_digest"), limit=128) or None
        ),
        "source_gate_digest": (
            _text(sealed_gate.get("gate_digest"), limit=128) or None
        ),
        "source_handoff_simulation_digest": (
            _text(sealed_handoff.get("simulation_digest"), limit=128) or None
        ),
        "source_rollback_digest": (
            _text(sealed_rollback.get("rollback_digest"), limit=128) or None
        ),
        "source_activation_request_digest": (
            _text(sealed_request.get("request_digest"), limit=128) or None
        ),
        "current_identity": {
            field: _text((current_identity or {}).get(field), limit=500) or None
            for field in _IDENTITY_FIELDS
        },
        "current_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "candidate_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,
        "selected_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "would_select_if_separately_activated": TYPED_DEPENDENCY_AUTHORITY,
        "activation_candidate_ready": ready,
        "rollback_drill_verified": bool(
            ready
            and sealed_handoff.get("status") == "ROLLBACK_DRILL_COMPLETE"
            and rollback_integrity.get("ok")
        ),
        "single_authority_invariant": True,
        "requires_separate_runtime_activation_boundary": True,
        "runtime_activation_authority_granted": False,
        "activation_performed": False,
        "cutover_performed": False,
        "runtime_reversion_performed": False,
        "changes_current_dependency_blocking": False,
        "changes_allowed_capability_tools": False,
        "blocks_execution": False,
        "creates_permit": False,
        "mutates_semantics": False,
        "mutates_business_state": False,
        "errors": unique_errors,
    }
    payload["preflight_digest"] = _digest(payload)
    return payload


def dependency_activation_preflight_integrity(
    preflight: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate sealed Stage 4D evidence and preserve the non-activation boundary."""

    row = deepcopy(preflight) if isinstance(preflight, dict) else {}
    errors: list[str] = []

    if row.get("version") != DEPENDENCY_ACTIVATION_PREFLIGHT_VERSION:
        errors.append("ACTIVATION_PREFLIGHT_VERSION_INVALID")
    if row.get("authority") != DEPENDENCY_ACTIVATION_PREFLIGHT_AUTHORITY:
        errors.append("ACTIVATION_PREFLIGHT_AUTHORITY_INVALID")
    errors.extend(
        _sealed_digest_errors(
            row,
            digest_field="preflight_digest",
            missing_code="ACTIVATION_PREFLIGHT_DIGEST_REQUIRED",
            invalid_code="ACTIVATION_PREFLIGHT_DIGEST_INVALID",
        )
    )

    if (
        row.get("current_runtime_dependency_authority")
        != LEGACY_DEPENDENCY_AUTHORITY
    ):
        errors.append("ACTIVATION_PREFLIGHT_CURRENT_AUTHORITY_MUST_BE_LEGACY")
    if (
        row.get("selected_runtime_dependency_authority")
        != LEGACY_DEPENDENCY_AUTHORITY
    ):
        errors.append("ACTIVATION_PREFLIGHT_SELECTED_AUTHORITY_MUST_BE_LEGACY")
    if row.get("candidate_dependency_authority") != TYPED_DEPENDENCY_AUTHORITY:
        errors.append("ACTIVATION_PREFLIGHT_CANDIDATE_AUTHORITY_INVALID")
    if row.get("would_select_if_separately_activated") != TYPED_DEPENDENCY_AUTHORITY:
        errors.append("ACTIVATION_PREFLIGHT_WOULD_SELECT_AUTHORITY_INVALID")
    if row.get("single_authority_invariant") is not True:
        errors.append("ACTIVATION_PREFLIGHT_SINGLE_AUTHORITY_REQUIRED")
    if row.get("requires_separate_runtime_activation_boundary") is not True:
        errors.append("ACTIVATION_PREFLIGHT_SEPARATE_BOUNDARY_REQUIRED")

    for field in _RUNTIME_FALSE_FIELDS:
        if bool(row.get(field)):
            errors.append(f"{field.upper()}_MUST_BE_FALSE")

    status = _text(row.get("status"), limit=120)
    evidence_errors = (
        row.get("errors") if isinstance(row.get("errors"), list) else []
    )
    if status == "ACTIVATION_PREFLIGHT_READY":
        if row.get("activation_candidate_ready") is not True:
            errors.append("ACTIVATION_PREFLIGHT_READY_FLAG_REQUIRED")
        if row.get("rollback_drill_verified") is not True:
            errors.append("ACTIVATION_PREFLIGHT_ROLLBACK_DRILL_REQUIRED")
        if evidence_errors:
            errors.append("ACTIVATION_PREFLIGHT_READY_ERRORS_MUST_BE_EMPTY")
        for field in (
            "source_attestation_digest",
            "source_gate_digest",
            "source_handoff_simulation_digest",
            "source_rollback_digest",
            "source_activation_request_digest",
        ):
            if not _text(row.get(field), limit=128):
                errors.append(f"{field.upper()}_REQUIRED")
    elif status == "BLOCKED":
        if row.get("activation_candidate_ready") is not False:
            errors.append("ACTIVATION_PREFLIGHT_BLOCKED_FLAG_INVALID")
        if not evidence_errors:
            errors.append("ACTIVATION_PREFLIGHT_BLOCKED_ERRORS_REQUIRED")
    else:
        errors.append("ACTIVATION_PREFLIGHT_STATUS_INVALID")

    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "preflight_digest": _text(row.get("preflight_digest"), limit=128) or None,
    }


__all__ = [
    "DEPENDENCY_ACTIVATION_PREFLIGHT_AUTHORITY",
    "DEPENDENCY_ACTIVATION_PREFLIGHT_VERSION",
    "DEPENDENCY_ACTIVATION_REQUEST_AUTHORITY",
    "DEPENDENCY_ACTIVATION_REQUEST_VERSION",
    "dependency_activation_preflight_integrity",
    "dependency_activation_request_integrity",
    "evaluate_dependency_activation_preflight",
]
