from __future__ import annotations

"""Deterministic validation for model-proposed semantic state changes.

The model remains the only open-language semantic compiler.  This module does
not interpret phrases such as "先别管" or "继续".  It only verifies that a
proposed state operation is grounded in the current user message, points to a
known object, uses the current revision, and changes only fields explicitly
allowed by the semantic state contract.
"""

from copy import deepcopy
from typing import Any, Iterable

_MODEL_SETTABLE_LIFECYCLES = {"ACTIVE", "PAUSED", "CANCELLED"}
_ACTIVE_GOAL_LIFECYCLES = {"OPEN", "ACTIVE", "BLOCKED", "PAUSED"}
_PATCHABLE_GOAL_FIELDS = {"target_candidate", "input_candidates", "condition", "depends_on"}
_GOAL_CHANGE_OPERATIONS = {"SET_GOAL_LIFECYCLE", "PATCH_GOAL", "SUPERSEDE_GOAL"}
_FOCUS_CHANGE_OPERATIONS = {"SET_GOAL_FOCUS", "SET_INTERACTION_FOCUS", "CLEAR_FOCUS"}


def _text(value: Any, *, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _revision(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 0 else default


def record_revision(record: dict[str, Any] | None) -> int:
    """Return the compatibility revision for one existing GoalRecord.

    Pre-V20.12 checkpoints did not persist a revision.  They are read as
    revision 1.  New records always persist an explicit positive revision.
    """
    if not isinstance(record, dict):
        return 0
    return max(1, _revision(record.get("revision"), default=1))


def focus_revision(focus_state: dict[str, Any] | None) -> int:
    """Return the revision of the non-authoritative UI focus projection."""
    if not isinstance(focus_state, dict):
        return 0
    return _revision(focus_state.get("revision"), default=0)


def _literal_evidence(
    *,
    raw: dict[str, Any],
    user_text: str,
    error_code: str,
    errors: list[str],
) -> str:
    evidence = _text(raw.get("evidence_span"), limit=500)
    if not evidence or evidence not in user_text:
        errors.append(error_code)
    return evidence


def validate_goal_changes(
    raw_changes: Iterable[dict[str, Any]],
    *,
    user_text: str,
    goal_records: Iterable[dict[str, Any]],
    proposal_goal_ids: Iterable[str],
    turn: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate concrete operations against current GoalRecord revisions.

    The function returns normalized operations plus deterministic error codes.
    It never chooses a Goal from language and never rewrites a requested effect.
    """
    records = [row for row in list(goal_records or []) if isinstance(row, dict)]
    by_id = {
        _text(row.get("goal_id"), limit=200): row
        for row in records
        if _text(row.get("goal_id"), limit=200)
    }
    proposed = {_text(value, limit=200) for value in proposal_goal_ids if _text(value, limit=200)}
    known_dependencies = set(by_id) | proposed
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_goal_ids: set[str] = set()

    for index, raw in enumerate(list(raw_changes or []), start=1):
        if not isinstance(raw, dict):
            errors.append(f"invalid_goal_change:{index}")
            continue
        operation = _text(raw.get("operation"), limit=80).upper()
        goal_id = _text(raw.get("goal_id"), limit=200)
        subject = f"goal_change:{goal_id or index}"
        if operation not in _GOAL_CHANGE_OPERATIONS:
            errors.append(f"unsupported_goal_change_operation:{operation or 'missing'}")
            continue
        if not goal_id:
            errors.append(f"goal_change_goal_id_required:{index}")
            continue
        if goal_id in seen_goal_ids:
            errors.append(f"duplicate_goal_change:{goal_id}")
            continue
        seen_goal_ids.add(goal_id)
        record = by_id.get(goal_id)
        if record is None:
            errors.append(f"unknown_goal_for_change:{goal_id}")
            continue

        evidence = _literal_evidence(
            raw=raw,
            user_text=user_text,
            error_code=f"goal_change_evidence_not_in_current_turn:{goal_id}",
            errors=errors,
        )
        actual_revision = record_revision(record)
        expected_revision = _revision(raw.get("expected_revision"), default=-1)
        if expected_revision < 1:
            errors.append(f"goal_expected_revision_required:{goal_id}")
        elif expected_revision != actual_revision:
            errors.append(
                f"goal_revision_conflict:{goal_id}:expected={expected_revision}:actual={actual_revision}"
            )

        base = {
            "operation": operation,
            "goal_id": goal_id,
            "expected_revision": expected_revision,
            "validated_against_revision": actual_revision,
            "next_revision": actual_revision + 1,
            "evidence_span": evidence,
            "evidence_turn": int(turn),
        }

        if operation == "SET_GOAL_LIFECYCLE":
            before = _text(raw.get("from"), limit=40).upper()
            after = _text(raw.get("to"), limit=40).upper()
            actual_lifecycle = _text(record.get("lifecycle") or "OPEN", limit=40).upper()
            if not before:
                errors.append(f"goal_lifecycle_from_required:{goal_id}")
            elif before != actual_lifecycle:
                errors.append(
                    f"goal_lifecycle_from_mismatch:{goal_id}:expected={before}:actual={actual_lifecycle}"
                )
            if after not in _MODEL_SETTABLE_LIFECYCLES:
                errors.append(f"goal_lifecycle_target_not_user_settable:{goal_id}:{after or 'missing'}")
            normalized.append({**base, "from": before, "to": after})
            continue

        if operation == "PATCH_GOAL":
            patch = raw.get("patch") if isinstance(raw.get("patch"), dict) else {}
            if not patch:
                errors.append(f"goal_patch_required:{goal_id}")
            for key in sorted(patch):
                if key not in _PATCHABLE_GOAL_FIELDS:
                    errors.append(f"goal_patch_forbidden_field:{goal_id}:{key}")
            normalized_patch = {
                key: deepcopy(value)
                for key, value in patch.items()
                if key in _PATCHABLE_GOAL_FIELDS
            }
            if "depends_on" in normalized_patch:
                raw_dependencies = normalized_patch.get("depends_on")
                if not isinstance(raw_dependencies, list):
                    errors.append(f"goal_patch_depends_on_must_be_array:{goal_id}")
                    normalized_patch["depends_on"] = []
                else:
                    dependencies = list(dict.fromkeys(
                        _text(value, limit=200)
                        for value in raw_dependencies
                        if _text(value, limit=200)
                    ))
                    for dependency in dependencies:
                        if dependency == goal_id:
                            errors.append(f"goal_patch_self_dependency:{goal_id}")
                        elif dependency not in known_dependencies:
                            errors.append(f"goal_patch_unknown_dependency:{goal_id}:{dependency}")
                    normalized_patch["depends_on"] = dependencies
            normalized.append({**base, "patch": normalized_patch})
            continue

        superseded_by = _text(raw.get("superseded_by"), limit=200)
        if not superseded_by:
            errors.append(f"goal_superseded_by_required:{goal_id}")
        elif superseded_by == goal_id:
            errors.append(f"goal_cannot_supersede_itself:{goal_id}")
        elif superseded_by not in proposed:
            errors.append(f"goal_superseded_by_not_in_current_proposal:{goal_id}:{superseded_by}")
        normalized.append({**base, "superseded_by": superseded_by})

    return normalized, errors


def validate_focus_change(
    raw_change: dict[str, Any] | None,
    *,
    user_text: str,
    focus_state: dict[str, Any] | None,
    goal_records: Iterable[dict[str, Any]],
    active_interaction_id: str | None,
    turn: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate a finite UI-focus operation without assigning semantic authority."""
    if raw_change is None:
        return None, []
    if not isinstance(raw_change, dict):
        return None, ["invalid_focus_change"]

    errors: list[str] = []
    operation = _text(raw_change.get("operation"), limit=80).upper()
    if operation not in _FOCUS_CHANGE_OPERATIONS:
        errors.append(f"unsupported_focus_change_operation:{operation or 'missing'}")

    evidence = _literal_evidence(
        raw=raw_change,
        user_text=user_text,
        error_code="focus_change_evidence_not_in_current_turn",
        errors=errors,
    )
    actual_revision = focus_revision(focus_state)
    expected_revision = _revision(raw_change.get("expected_revision"), default=-1)
    if expected_revision < 0:
        errors.append("focus_expected_revision_required")
    elif expected_revision != actual_revision:
        errors.append(f"focus_revision_conflict:expected={expected_revision}:actual={actual_revision}")

    normalized: dict[str, Any] = {
        "operation": operation,
        "expected_revision": expected_revision,
        "validated_against_focus_revision": actual_revision,
        "next_focus_revision": actual_revision + 1,
        "evidence_span": evidence,
        "evidence_turn": int(turn),
    }
    records = {
        _text(row.get("goal_id"), limit=200): row
        for row in list(goal_records or [])
        if isinstance(row, dict) and _text(row.get("goal_id"), limit=200)
    }

    if operation == "SET_GOAL_FOCUS":
        goal_id = _text(raw_change.get("goal_id"), limit=200)
        record = records.get(goal_id)
        if record is None:
            errors.append(f"unknown_goal_for_focus:{goal_id or 'missing'}")
        elif _text(record.get("lifecycle") or "OPEN", limit=40).upper() not in _ACTIVE_GOAL_LIFECYCLES:
            errors.append(f"goal_not_active_for_focus:{goal_id}")
        normalized["goal_id"] = goal_id
    elif operation == "SET_INTERACTION_FOCUS":
        interaction_id = _text(raw_change.get("interaction_id"), limit=300)
        active_id = _text(active_interaction_id, limit=300)
        if not interaction_id:
            errors.append("interaction_id_required_for_focus")
        elif not active_id or interaction_id != active_id:
            errors.append(f"interaction_not_active_for_focus:{interaction_id}")
        normalized["interaction_id"] = interaction_id
    elif operation == "CLEAR_FOCUS":
        pass

    return normalized, errors


def apply_focus_change(
    focus_state: dict[str, Any] | None,
    change: dict[str, Any] | None,
    *,
    turn: int,
) -> dict[str, Any] | None:
    """Apply a previously validated focus operation with optimistic revision checking."""
    if not isinstance(change, dict):
        return deepcopy(focus_state) if isinstance(focus_state, dict) else None
    actual_revision = focus_revision(focus_state)
    expected_revision = _revision(change.get("validated_against_focus_revision"), default=-1)
    if expected_revision != actual_revision:
        raise ValueError(
            f"focus_revision_conflict:expected={expected_revision}:actual={actual_revision}"
        )
    operation = _text(change.get("operation"), limit=80).upper()
    if operation not in _FOCUS_CHANGE_OPERATIONS:
        raise ValueError(f"unsupported_focus_change_operation:{operation or 'missing'}")
    next_revision = actual_revision + 1
    common = {
        "revision": next_revision,
        "updated_turn": int(turn),
        "last_change_operation": operation,
        "last_change_evidence_span": _text(change.get("evidence_span"), limit=500),
    }
    if operation == "SET_GOAL_FOCUS":
        return {**common, "focused_goal_id": _text(change.get("goal_id"), limit=200)}
    if operation == "SET_INTERACTION_FOCUS":
        return {**common, "focused_interaction_id": _text(change.get("interaction_id"), limit=300)}
    return {**common, "focused_goal_id": None, "focused_interaction_id": None}


__all__ = [
    "apply_focus_change",
    "focus_revision",
    "record_revision",
    "validate_focus_change",
    "validate_goal_changes",
]
