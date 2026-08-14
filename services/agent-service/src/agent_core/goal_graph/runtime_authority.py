from __future__ import annotations

"""Explicit Stage 4E runtime dependency-authority selector.

The selector is the first boundary allowed to choose verified typed dataflow as
*runtime dependency* authority.  It remains deliberately narrow: it does not
create an ExecutionPermit, dispatch a tool, mutate semantic/business state, or
activate itself.  Without an exact sealed Stage 4D preflight plus an explicit
external runtime activation record, selection fails closed to the legacy
``depends_on_goal_ids`` authority.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Iterable

from .activation_preflight import dependency_activation_preflight_integrity
from .cutover_gate import LEGACY_DEPENDENCY_AUTHORITY, TYPED_DEPENDENCY_AUTHORITY

DEPENDENCY_RUNTIME_ACTIVATION_VERSION = "typed-dependency-runtime-activation@1"
DEPENDENCY_RUNTIME_ACTIVATION_AUTHORITY = "external_governance_runtime_activation"
DEPENDENCY_RUNTIME_SELECTION_VERSION = "typed-dependency-runtime-selection@1"
DEPENDENCY_RUNTIME_SELECTION_AUTHORITY = "runtime_dependency_authority_selector"

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


def dependency_runtime_activation_integrity(
    activation: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate an explicit, identity-bound runtime activation record."""

    row = deepcopy(activation) if isinstance(activation, dict) else {}
    errors: list[str] = []
    if row.get("version") != DEPENDENCY_RUNTIME_ACTIVATION_VERSION:
        errors.append("RUNTIME_ACTIVATION_VERSION_INVALID")
    if row.get("authority") != DEPENDENCY_RUNTIME_ACTIVATION_AUTHORITY:
        errors.append("RUNTIME_ACTIVATION_AUTHORITY_INVALID")
    if row.get("immutable") is not True:
        errors.append("RUNTIME_ACTIVATION_IMMUTABLE_REQUIRED")
    if _text(row.get("status"), limit=80) != "ACTIVATED":
        errors.append("RUNTIME_ACTIVATION_STATUS_INVALID")
    if row.get("external_authority_verified") is not True:
        errors.append("RUNTIME_ACTIVATION_EXTERNAL_AUTHORITY_REQUIRED")
    if not _text(row.get("activation_id"), limit=500):
        errors.append("RUNTIME_ACTIVATION_ID_REQUIRED")
    if not _text(row.get("issued_by"), limit=500):
        errors.append("RUNTIME_ACTIVATION_ISSUER_REQUIRED")
    if not _text(row.get("preflight_digest"), limit=128):
        errors.append("RUNTIME_ACTIVATION_PREFLIGHT_DIGEST_REQUIRED")
    if row.get("desired_dependency_authority") != TYPED_DEPENDENCY_AUTHORITY:
        errors.append("RUNTIME_ACTIVATION_DESIRED_AUTHORITY_INVALID")
    if (
        row.get("expected_current_runtime_dependency_authority")
        != LEGACY_DEPENDENCY_AUTHORITY
    ):
        errors.append("RUNTIME_ACTIVATION_EXPECTED_CURRENT_AUTHORITY_INVALID")
    for field in _IDENTITY_FIELDS:
        if not _text(row.get(field), limit=500):
            errors.append(f"RUNTIME_ACTIVATION_{field.upper()}_REQUIRED")
    try:
        expires_at = float(row.get("expires_at"))
    except (TypeError, ValueError):
        expires_at = 0.0
    if expires_at <= 0:
        errors.append("RUNTIME_ACTIVATION_EXPIRY_REQUIRED")
    errors.extend(
        _sealed_digest_errors(
            row,
            digest_field="activation_digest",
            missing_code="RUNTIME_ACTIVATION_DIGEST_REQUIRED",
            invalid_code="RUNTIME_ACTIVATION_DIGEST_INVALID",
        )
    )
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "activation_digest": _text(row.get("activation_digest"), limit=128) or None,
    }


def _selection_binding_errors(
    *,
    preflight: dict[str, Any],
    activation: dict[str, Any],
    current_identity: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    preflight_digest = _text(preflight.get("preflight_digest"), limit=128)
    if _text(activation.get("preflight_digest"), limit=128) != preflight_digest:
        errors.append("RUNTIME_ACTIVATION_PREFLIGHT_DIGEST_MISMATCH")

    preflight_identity = (
        preflight.get("current_identity")
        if isinstance(preflight.get("current_identity"), dict)
        else {}
    )
    current = current_identity if isinstance(current_identity, dict) else {}
    for field in _IDENTITY_FIELDS:
        expected = _text(preflight_identity.get(field), limit=500)
        actual = _text(current.get(field), limit=500)
        if not actual:
            errors.append(f"CURRENT_{field.upper()}_REQUIRED")
        elif actual != expected:
            errors.append(f"CURRENT_{field.upper()}_MISMATCH")
        if _text(activation.get(field), limit=500) != expected:
            errors.append(f"RUNTIME_ACTIVATION_{field.upper()}_MISMATCH")
    return errors


def select_runtime_dependency_authority(
    *,
    preflight: dict[str, Any] | None,
    activation: dict[str, Any] | None,
    current_identity: dict[str, Any] | None,
    evaluation_time: float | None,
    rollback_requested: bool = False,
) -> dict[str, Any]:
    """Select exactly one dependency authority, defaulting/failing to legacy."""

    sealed_preflight = deepcopy(preflight) if isinstance(preflight, dict) else {}
    sealed_activation = deepcopy(activation) if isinstance(activation, dict) else {}
    explicit_attempt = bool(sealed_preflight or sealed_activation)
    errors: list[str] = []

    if explicit_attempt:
        preflight_integrity = dependency_activation_preflight_integrity(sealed_preflight)
        if not preflight_integrity.get("ok"):
            errors.extend(
                f"PREFLIGHT:{code}"
                for code in list(preflight_integrity.get("errors") or [])
            )
        if sealed_preflight.get("status") != "ACTIVATION_PREFLIGHT_READY":
            errors.append("ACTIVATION_PREFLIGHT_NOT_READY")
        if sealed_preflight.get("activation_candidate_ready") is not True:
            errors.append("ACTIVATION_PREFLIGHT_CANDIDATE_FLAG_REQUIRED")
        if sealed_preflight.get("rollback_drill_verified") is not True:
            errors.append("ACTIVATION_PREFLIGHT_ROLLBACK_DRILL_REQUIRED")
        if (
            sealed_preflight.get("selected_runtime_dependency_authority")
            != LEGACY_DEPENDENCY_AUTHORITY
        ):
            errors.append("ACTIVATION_PREFLIGHT_LEGACY_SOURCE_REQUIRED")
        if (
            sealed_preflight.get("would_select_if_separately_activated")
            != TYPED_DEPENDENCY_AUTHORITY
        ):
            errors.append("ACTIVATION_PREFLIGHT_TYPED_CANDIDATE_REQUIRED")

        activation_integrity = dependency_runtime_activation_integrity(sealed_activation)
        if not activation_integrity.get("ok"):
            errors.extend(
                f"ACTIVATION:{code}"
                for code in list(activation_integrity.get("errors") or [])
            )
        errors.extend(
            _selection_binding_errors(
                preflight=sealed_preflight,
                activation=sealed_activation,
                current_identity=current_identity,
            )
        )
        if evaluation_time is None:
            errors.append("RUNTIME_AUTHORITY_EVALUATION_TIME_REQUIRED")
        else:
            try:
                now = float(evaluation_time)
            except (TypeError, ValueError):
                errors.append("RUNTIME_AUTHORITY_EVALUATION_TIME_INVALID")
            else:
                try:
                    expires_at = float(sealed_activation.get("expires_at"))
                except (TypeError, ValueError):
                    expires_at = 0.0
                if expires_at > 0 and now >= expires_at:
                    errors.append("RUNTIME_ACTIVATION_EXPIRED")

    unique_errors = sorted(set(errors))
    typed_selected = bool(explicit_attempt and not unique_errors and not rollback_requested)
    selected = TYPED_DEPENDENCY_AUTHORITY if typed_selected else LEGACY_DEPENDENCY_AUTHORITY
    if rollback_requested:
        status = "ROLLED_BACK_TO_LEGACY"
    elif typed_selected:
        status = "TYPED_AUTHORITY_ACTIVE"
    elif explicit_attempt:
        status = "LEGACY_FAIL_CLOSED"
    else:
        status = "LEGACY_DEFAULT"

    payload: dict[str, Any] = {
        "version": DEPENDENCY_RUNTIME_SELECTION_VERSION,
        "authority": DEPENDENCY_RUNTIME_SELECTION_AUTHORITY,
        "status": status,
        "source_preflight_digest": (
            _text(sealed_preflight.get("preflight_digest"), limit=128) or None
        ),
        "source_activation_digest": (
            _text(sealed_activation.get("activation_digest"), limit=128) or None
        ),
        "current_identity": {
            field: _text((current_identity or {}).get(field), limit=500) or None
            for field in _IDENTITY_FIELDS
        },
        "previous_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "candidate_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,
        "selected_runtime_dependency_authority": selected,
        "selected_authority_count": 1,
        "single_authority_invariant": True,
        "explicit_activation_present": explicit_attempt,
        "runtime_activation_authority_granted": typed_selected,
        "activation_performed": typed_selected,
        "cutover_performed": typed_selected,
        "rollback_requested": bool(rollback_requested),
        "runtime_reversion_performed": bool(rollback_requested),
        "changes_current_dependency_blocking": typed_selected,
        "changes_allowed_capability_tools": False,
        "blocks_execution": False,
        "creates_permit": False,
        "dispatches_tools": False,
        "mutates_semantics": False,
        "mutates_business_state": False,
        "errors": unique_errors,
    }
    payload["selection_digest"] = _digest(payload)
    return payload


def dependency_runtime_selection_integrity(
    selection: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate a sealed selector result and its exactly-one-authority invariant."""

    row = deepcopy(selection) if isinstance(selection, dict) else {}
    errors: list[str] = []
    if row.get("version") != DEPENDENCY_RUNTIME_SELECTION_VERSION:
        errors.append("RUNTIME_SELECTION_VERSION_INVALID")
    if row.get("authority") != DEPENDENCY_RUNTIME_SELECTION_AUTHORITY:
        errors.append("RUNTIME_SELECTION_AUTHORITY_INVALID")
    errors.extend(
        _sealed_digest_errors(
            row,
            digest_field="selection_digest",
            missing_code="RUNTIME_SELECTION_DIGEST_REQUIRED",
            invalid_code="RUNTIME_SELECTION_DIGEST_INVALID",
        )
    )
    selected = row.get("selected_runtime_dependency_authority")
    if selected not in {LEGACY_DEPENDENCY_AUTHORITY, TYPED_DEPENDENCY_AUTHORITY}:
        errors.append("RUNTIME_SELECTION_AUTHORITY_UNKNOWN")
    if row.get("selected_authority_count") != 1:
        errors.append("RUNTIME_SELECTION_EXACTLY_ONE_REQUIRED")
    if row.get("single_authority_invariant") is not True:
        errors.append("RUNTIME_SELECTION_SINGLE_AUTHORITY_REQUIRED")
    if bool(row.get("creates_permit")):
        errors.append("RUNTIME_SELECTION_CREATES_PERMIT_MUST_BE_FALSE")
    if bool(row.get("dispatches_tools")):
        errors.append("RUNTIME_SELECTION_DISPATCHES_TOOLS_MUST_BE_FALSE")
    if bool(row.get("mutates_semantics")):
        errors.append("RUNTIME_SELECTION_MUTATES_SEMANTICS_MUST_BE_FALSE")
    if bool(row.get("mutates_business_state")):
        errors.append("RUNTIME_SELECTION_MUTATES_BUSINESS_STATE_MUST_BE_FALSE")

    status = _text(row.get("status"), limit=120)
    evidence_errors = row.get("errors") if isinstance(row.get("errors"), list) else []
    if status == "TYPED_AUTHORITY_ACTIVE":
        if selected != TYPED_DEPENDENCY_AUTHORITY:
            errors.append("RUNTIME_SELECTION_TYPED_STATUS_AUTHORITY_MISMATCH")
        if row.get("runtime_activation_authority_granted") is not True:
            errors.append("RUNTIME_SELECTION_TYPED_GRANT_REQUIRED")
        if row.get("activation_performed") is not True or row.get("cutover_performed") is not True:
            errors.append("RUNTIME_SELECTION_TYPED_ACTIVATION_REQUIRED")
        if evidence_errors:
            errors.append("RUNTIME_SELECTION_TYPED_ERRORS_MUST_BE_EMPTY")
        if not _text(row.get("source_preflight_digest"), limit=128):
            errors.append("RUNTIME_SELECTION_PREFLIGHT_DIGEST_REQUIRED")
        if not _text(row.get("source_activation_digest"), limit=128):
            errors.append("RUNTIME_SELECTION_ACTIVATION_DIGEST_REQUIRED")
    elif status in {"LEGACY_DEFAULT", "LEGACY_FAIL_CLOSED", "ROLLED_BACK_TO_LEGACY"}:
        if selected != LEGACY_DEPENDENCY_AUTHORITY:
            errors.append("RUNTIME_SELECTION_LEGACY_STATUS_AUTHORITY_MISMATCH")
        if bool(row.get("runtime_activation_authority_granted")):
            errors.append("RUNTIME_SELECTION_LEGACY_GRANT_MUST_BE_FALSE")
        if bool(row.get("activation_performed")) or bool(row.get("cutover_performed")):
            errors.append("RUNTIME_SELECTION_LEGACY_ACTIVATION_MUST_BE_FALSE")
        if status == "LEGACY_DEFAULT" and evidence_errors:
            errors.append("RUNTIME_SELECTION_DEFAULT_ERRORS_MUST_BE_EMPTY")
        if status == "LEGACY_FAIL_CLOSED" and not evidence_errors:
            errors.append("RUNTIME_SELECTION_FAIL_CLOSED_ERRORS_REQUIRED")
        if status == "ROLLED_BACK_TO_LEGACY" and row.get("rollback_requested") is not True:
            errors.append("RUNTIME_SELECTION_ROLLBACK_REQUEST_REQUIRED")
    else:
        errors.append("RUNTIME_SELECTION_STATUS_INVALID")

    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "selection_digest": _text(row.get("selection_digest"), limit=128) or None,
    }


def selected_dependency_goal_ids(
    *,
    selection: dict[str, Any] | None,
    legacy_dependency_goal_ids: Iterable[str],
    typed_dependency_goal_ids: Iterable[str],
) -> list[str]:
    """Return one authority's dependency set; never union/intersect both sets."""

    legacy = sorted({str(value) for value in legacy_dependency_goal_ids if str(value)})
    typed = sorted({str(value) for value in typed_dependency_goal_ids if str(value)})
    integrity = dependency_runtime_selection_integrity(selection)
    if (
        integrity.get("ok")
        and isinstance(selection, dict)
        and selection.get("selected_runtime_dependency_authority")
        == TYPED_DEPENDENCY_AUTHORITY
    ):
        return typed
    return legacy


__all__ = [
    "DEPENDENCY_RUNTIME_ACTIVATION_AUTHORITY",
    "DEPENDENCY_RUNTIME_ACTIVATION_VERSION",
    "DEPENDENCY_RUNTIME_SELECTION_AUTHORITY",
    "DEPENDENCY_RUNTIME_SELECTION_VERSION",
    "dependency_runtime_activation_integrity",
    "dependency_runtime_selection_integrity",
    "select_runtime_dependency_authority",
    "selected_dependency_goal_ids",
]
