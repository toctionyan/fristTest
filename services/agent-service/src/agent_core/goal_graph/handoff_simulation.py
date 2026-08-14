from __future__ import annotations

"""Simulation-only dependency-authority handoff evidence.

Stage 4C exercises a singular legacy -> typed -> legacy authority timeline without
activating any production authority.  The observed runtime authority is always
legacy; typed authority may appear only inside the sealed simulation timeline
when the Stage 4B readiness gate is valid and candidate-ready.
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

DEPENDENCY_AUTHORITY_HANDOFF_SIMULATION_VERSION = (
    "typed-dependency-authority-handoff-simulation@1"
)
DEPENDENCY_AUTHORITY_HANDOFF_SIMULATION_AUTHORITY = (
    "simulation_only_single_authority_handoff_not_runtime_authority"
)

_ALLOWED_AUTHORITIES = {
    LEGACY_DEPENDENCY_AUTHORITY,
    TYPED_DEPENDENCY_AUTHORITY,
}
_RUNTIME_FALSE_FIELDS = (
    "runtime_activation_authority_granted",
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


def _timeline_step(*, index: int, phase: str, authority: str) -> dict[str, Any]:
    """Create one simulated point with exactly one active authority."""

    return {
        "index": index,
        "phase": phase,
        "selected_dependency_authority": authority,
        "active_authorities": [authority],
        "legacy_active": authority == LEGACY_DEPENDENCY_AUTHORITY,
        "typed_active": authority == TYPED_DEPENDENCY_AUTHORITY,
    }


def _rollback_errors(
    *,
    gate: dict[str, Any],
    rollback: dict[str, Any] | None,
) -> list[str]:
    row = deepcopy(rollback) if isinstance(rollback, dict) else {}
    integrity = dependency_authority_rollback_integrity(row)
    errors: list[str] = []
    if not integrity.get("ok"):
        errors.extend(
            f"ROLLBACK:{code}" for code in list(integrity.get("errors") or [])
        )
    if _text(row.get("source_gate_digest"), limit=128) != _text(
        gate.get("gate_digest"), limit=128
    ):
        errors.append("ROLLBACK_SOURCE_GATE_DIGEST_MISMATCH")
    if row.get("reversion_target") != LEGACY_DEPENDENCY_AUTHORITY:
        errors.append("ROLLBACK_REVERSION_TARGET_INVALID")
    return errors


def build_dependency_authority_handoff_simulation(
    *,
    gate: dict[str, Any] | None,
    requested_simulated_authority: str = TYPED_DEPENDENCY_AUTHORITY,
    rollback: dict[str, Any] | None = None,
    exercise_rollback: bool = False,
) -> dict[str, Any]:
    """Build sealed dry-run evidence while leaving real runtime authority legacy.

    If typed authority is requested, it is entered only in the simulation when
    the Stage 4B gate is structurally valid and reports an exact candidate-ready
    state.  When ``exercise_rollback`` is true, the rollback contract is fully
    validated *before* the simulated typed transition; an invalid rollback drill
    therefore stays legacy for the entire simulation.
    """

    sealed_gate = deepcopy(gate) if isinstance(gate, dict) else {}
    sealed_rollback = deepcopy(rollback) if isinstance(rollback, dict) else {}
    requested = _text(requested_simulated_authority, limit=200)
    errors: list[str] = []

    gate_integrity = dependency_cutover_gate_integrity(sealed_gate)
    if not gate_integrity.get("ok"):
        errors.extend(
            f"CUTOVER_GATE:{code}" for code in list(gate_integrity.get("errors") or [])
        )

    if requested not in _ALLOWED_AUTHORITIES:
        errors.append("SIMULATED_AUTHORITY_REQUEST_INVALID")

    candidate_ready = bool(
        gate_integrity.get("ok")
        and sealed_gate.get("status") == "CANDIDATE_READY"
        and sealed_gate.get("cutover_candidate_ready") is True
        and sealed_gate.get("grant_shape_and_binding_accepted") is True
        and sealed_gate.get("candidate_dependency_authority")
        == TYPED_DEPENDENCY_AUTHORITY
        and sealed_gate.get("selected_runtime_dependency_authority")
        == LEGACY_DEPENDENCY_AUTHORITY
    )

    if requested == TYPED_DEPENDENCY_AUTHORITY and not candidate_ready:
        errors.append("TYPED_HANDOFF_CANDIDATE_NOT_READY")

    if exercise_rollback:
        errors.extend(_rollback_errors(gate=sealed_gate, rollback=sealed_rollback))

    unique_errors = sorted(set(errors))
    timeline = [
        _timeline_step(
            index=0,
            phase="observed_runtime_baseline",
            authority=LEGACY_DEPENDENCY_AUTHORITY,
        )
    ]
    typed_entered = False
    rollback_exercised = False

    if not unique_errors and requested == TYPED_DEPENDENCY_AUTHORITY:
        timeline.append(
            _timeline_step(
                index=len(timeline),
                phase="simulated_typed_handoff",
                authority=TYPED_DEPENDENCY_AUTHORITY,
            )
        )
        typed_entered = True
        if exercise_rollback:
            timeline.append(
                _timeline_step(
                    index=len(timeline),
                    phase="simulated_rollback_to_legacy",
                    authority=LEGACY_DEPENDENCY_AUTHORITY,
                )
            )
            rollback_exercised = True

    if unique_errors:
        status = "BLOCKED"
    elif rollback_exercised:
        status = "ROLLBACK_DRILL_COMPLETE"
    elif typed_entered:
        status = "TYPED_HANDOFF_SIMULATED"
    else:
        status = "LEGACY_ONLY_SIMULATED"

    final_simulated_authority = timeline[-1]["selected_dependency_authority"]
    payload: dict[str, Any] = {
        "version": DEPENDENCY_AUTHORITY_HANDOFF_SIMULATION_VERSION,
        "authority": DEPENDENCY_AUTHORITY_HANDOFF_SIMULATION_AUTHORITY,
        "status": status,
        "simulation_only": True,
        "source_gate_digest": _text(sealed_gate.get("gate_digest"), limit=128) or None,
        "source_rollback_digest": (
            _text(sealed_rollback.get("rollback_digest"), limit=128) or None
        ),
        "requested_simulated_authority": requested or None,
        "observed_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "selected_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "candidate_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,
        "simulated_final_dependency_authority": final_simulated_authority,
        "typed_candidate_entered_in_simulation": typed_entered,
        "rollback_exercised_in_simulation": rollback_exercised,
        "single_authority_invariant": True,
        "dual_authority_observed": False,
        "timeline": timeline,
        "runtime_activation_authority_granted": False,
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
    payload["simulation_digest"] = _digest(payload)
    return payload


def dependency_authority_handoff_simulation_integrity(
    simulation: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate sealed simulation evidence and its single-authority invariant."""

    row = deepcopy(simulation) if isinstance(simulation, dict) else {}
    errors: list[str] = []

    if row.get("version") != DEPENDENCY_AUTHORITY_HANDOFF_SIMULATION_VERSION:
        errors.append("HANDOFF_SIMULATION_VERSION_INVALID")
    if row.get("authority") != DEPENDENCY_AUTHORITY_HANDOFF_SIMULATION_AUTHORITY:
        errors.append("HANDOFF_SIMULATION_AUTHORITY_INVALID")
    if row.get("simulation_only") is not True:
        errors.append("HANDOFF_SIMULATION_ONLY_REQUIRED")

    stored_digest = _text(row.get("simulation_digest"), limit=128)
    if not stored_digest:
        errors.append("HANDOFF_SIMULATION_DIGEST_REQUIRED")
    else:
        payload = deepcopy(row)
        payload.pop("simulation_digest", None)
        if stored_digest != _digest(payload):
            errors.append("HANDOFF_SIMULATION_DIGEST_INVALID")

    if row.get("observed_runtime_dependency_authority") != LEGACY_DEPENDENCY_AUTHORITY:
        errors.append("HANDOFF_OBSERVED_RUNTIME_AUTHORITY_MUST_BE_LEGACY")
    if row.get("selected_runtime_dependency_authority") != LEGACY_DEPENDENCY_AUTHORITY:
        errors.append("HANDOFF_SELECTED_RUNTIME_AUTHORITY_MUST_BE_LEGACY")
    if row.get("candidate_dependency_authority") != TYPED_DEPENDENCY_AUTHORITY:
        errors.append("HANDOFF_CANDIDATE_AUTHORITY_INVALID")
    if row.get("single_authority_invariant") is not True:
        errors.append("HANDOFF_SINGLE_AUTHORITY_REQUIRED")
    if row.get("dual_authority_observed") is not False:
        errors.append("HANDOFF_DUAL_AUTHORITY_MUST_BE_FALSE")

    for field in _RUNTIME_FALSE_FIELDS:
        if bool(row.get(field)):
            errors.append(f"{field.upper()}_MUST_BE_FALSE")

    status = _text(row.get("status"), limit=100)
    expected_timeline_by_status = {
        "BLOCKED": [
            ("observed_runtime_baseline", LEGACY_DEPENDENCY_AUTHORITY),
        ],
        "LEGACY_ONLY_SIMULATED": [
            ("observed_runtime_baseline", LEGACY_DEPENDENCY_AUTHORITY),
        ],
        "TYPED_HANDOFF_SIMULATED": [
            ("observed_runtime_baseline", LEGACY_DEPENDENCY_AUTHORITY),
            ("simulated_typed_handoff", TYPED_DEPENDENCY_AUTHORITY),
        ],
        "ROLLBACK_DRILL_COMPLETE": [
            ("observed_runtime_baseline", LEGACY_DEPENDENCY_AUTHORITY),
            ("simulated_typed_handoff", TYPED_DEPENDENCY_AUTHORITY),
            ("simulated_rollback_to_legacy", LEGACY_DEPENDENCY_AUTHORITY),
        ],
    }
    expected_timeline = expected_timeline_by_status.get(status)
    if expected_timeline is None:
        errors.append("HANDOFF_SIMULATION_STATUS_INVALID")

    timeline = row.get("timeline") if isinstance(row.get("timeline"), list) else []
    if not timeline:
        errors.append("HANDOFF_TIMELINE_REQUIRED")
    typed_steps = 0
    actual_timeline: list[tuple[str, str]] = []
    for index, step in enumerate(timeline):
        if not isinstance(step, dict):
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_INVALID")
            actual_timeline.append(("", ""))
            continue
        if step.get("index") != index:
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_INDEX_INVALID")
        phase = _text(step.get("phase"), limit=200)
        selected = _text(step.get("selected_dependency_authority"), limit=200)
        actual_timeline.append((phase, selected))
        active = list(step.get("active_authorities") or [])
        if selected not in _ALLOWED_AUTHORITIES:
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_AUTHORITY_INVALID")
        if active != [selected]:
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_NOT_SINGULAR")
        if bool(step.get("legacy_active")) == bool(step.get("typed_active")):
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_ACTIVE_FLAGS_INVALID")
        if selected == LEGACY_DEPENDENCY_AUTHORITY:
            if step.get("legacy_active") is not True or bool(step.get("typed_active")):
                errors.append(f"HANDOFF_TIMELINE_STEP_{index}_LEGACY_FLAGS_INVALID")
        elif selected == TYPED_DEPENDENCY_AUTHORITY:
            typed_steps += 1
            if step.get("typed_active") is not True or bool(step.get("legacy_active")):
                errors.append(f"HANDOFF_TIMELINE_STEP_{index}_TYPED_FLAGS_INVALID")

    if expected_timeline is not None and actual_timeline != expected_timeline:
        errors.append("HANDOFF_TIMELINE_SHAPE_INVALID")

    final = _text(row.get("simulated_final_dependency_authority"), limit=200)
    timeline_final = (
        _text(timeline[-1].get("selected_dependency_authority"), limit=200)
        if timeline and isinstance(timeline[-1], dict)
        else ""
    )
    if final != timeline_final:
        errors.append("HANDOFF_FINAL_AUTHORITY_TIMELINE_MISMATCH")
    if expected_timeline is not None and final != expected_timeline[-1][1]:
        errors.append("HANDOFF_FINAL_AUTHORITY_STATUS_MISMATCH")

    expected_typed_steps = 1 if status in {
        "TYPED_HANDOFF_SIMULATED",
        "ROLLBACK_DRILL_COMPLETE",
    } else 0
    if typed_steps != expected_typed_steps:
        errors.append("HANDOFF_TYPED_STEP_COUNT_INVALID")

    typed_entered = row.get("typed_candidate_entered_in_simulation") is True
    expected_typed_entered = status in {
        "TYPED_HANDOFF_SIMULATED",
        "ROLLBACK_DRILL_COMPLETE",
    }
    if typed_entered != expected_typed_entered:
        errors.append("HANDOFF_TYPED_ENTRY_FLAG_MISMATCH")

    rollback_exercised = row.get("rollback_exercised_in_simulation") is True
    expected_rollback_exercised = status == "ROLLBACK_DRILL_COMPLETE"
    if rollback_exercised != expected_rollback_exercised:
        errors.append("HANDOFF_ROLLBACK_FLAG_MISMATCH")

    requested = _text(row.get("requested_simulated_authority"), limit=200)
    if status == "LEGACY_ONLY_SIMULATED" and requested != LEGACY_DEPENDENCY_AUTHORITY:
        errors.append("HANDOFF_REQUEST_STATUS_MISMATCH")
    if status in {"TYPED_HANDOFF_SIMULATED", "ROLLBACK_DRILL_COMPLETE"} and requested != TYPED_DEPENDENCY_AUTHORITY:
        errors.append("HANDOFF_REQUEST_STATUS_MISMATCH")

    evidence_errors = row.get("errors") if isinstance(row.get("errors"), list) else []
    if status == "BLOCKED" and not evidence_errors:
        errors.append("HANDOFF_BLOCKED_ERRORS_REQUIRED")
    if status != "BLOCKED" and evidence_errors:
        errors.append("HANDOFF_NONBLOCKED_ERRORS_MUST_BE_EMPTY")

    if status != "BLOCKED" and not _text(row.get("source_gate_digest"), limit=128):
        errors.append("HANDOFF_SOURCE_GATE_DIGEST_REQUIRED")
    if status == "ROLLBACK_DRILL_COMPLETE" and not _text(
        row.get("source_rollback_digest"), limit=128
    ):
        errors.append("HANDOFF_SOURCE_ROLLBACK_DIGEST_REQUIRED")

    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "simulation_digest": stored_digest or None,
    }


__all__ = [
    "DEPENDENCY_AUTHORITY_HANDOFF_SIMULATION_AUTHORITY",
    "DEPENDENCY_AUTHORITY_HANDOFF_SIMULATION_VERSION",
    "build_dependency_authority_handoff_simulation",
    "dependency_authority_handoff_simulation_integrity",
]
