from __future__ import annotations

"""Canonical read-only lifecycle projections used by ContextBundle.

This module never mutates Goal, Blocker or Clarification state. Lifecycle keeps
all write and transition authority and re-exports these functions only for
backward-compatible imports.
"""

from copy import deepcopy
from typing import Any, Iterable

_ACTIVE_GOAL_LIFECYCLES = {"OPEN", "ACTIVE", "BLOCKED", "PAUSED"}
_ACTIVE_BLOCKER_STATUSES = {"OPEN"}
_ACTIVE_CLARIFICATION_STATUSES = {"pending", "resuming"}


def _record_revision(record: dict[str, Any] | None) -> int:
    if not isinstance(record, dict):
        return 0
    value = record.get("revision")
    if isinstance(value, bool):
        return 1
    try:
        revision = int(value)
    except (TypeError, ValueError):
        revision = 1
    return max(1, revision if revision >= 0 else 1)


def active_goal_blockers(
    state_or_blockers: dict[str, Any] | Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = state_or_blockers.get("goal_blockers") or [] if isinstance(state_or_blockers, dict) else state_or_blockers
    return [
        deepcopy(row)
        for row in list(rows or [])
        if isinstance(row, dict)
        and str(row.get("status") or "OPEN").upper() in _ACTIVE_BLOCKER_STATUSES
    ]


def _active_goal_records(
    state_or_records: dict[str, Any] | Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = state_or_records.get("goal_records") or [] if isinstance(state_or_records, dict) else state_or_records
    return [
        deepcopy(row)
        for row in list(rows or [])
        if isinstance(row, dict)
        and str(row.get("lifecycle") or "OPEN").upper() in _ACTIVE_GOAL_LIFECYCLES
    ]


def goal_records_context_projection(
    state: dict[str, Any], *, limit: int = 20
) -> list[dict[str, Any]]:
    rows = _active_goal_records(state)
    rows.sort(
        key=lambda row: (
            int(row.get("updated_turn") or 0),
            int(row.get("created_turn") or 0),
        ),
        reverse=True,
    )
    return [
        {
            "goal_id": row.get("goal_id"),
            "description": row.get("description"),
            "requested_effect": deepcopy(row.get("requested_effect"))
            if isinstance(row.get("requested_effect"), dict)
            else None,
            "lifecycle": row.get("lifecycle"),
            "revision": _record_revision(row),
            "depends_on": list(row.get("depends_on") or []),
            "continuation_of": row.get("continuation_of"),
            "completion_tool_names": list(row.get("completion_tool_names") or []),
            "updated_turn": row.get("updated_turn"),
            "authority": "semantic_goal_lifecycle_not_business_fact",
        }
        for row in rows[: max(0, int(limit))]
    ]


def _state_scope(state: dict[str, Any]) -> dict[str, str]:
    return {
        "tenant_id": str(state.get("current_tenant_id") or ""),
        "user_id": str(state.get("current_user_id") or ""),
        "thread_id": str(state.get("current_thread_id") or ""),
    }


def active_pending_clarification(state: dict[str, Any]) -> dict[str, Any] | None:
    if int(state.get("state_schema_version") or 1) >= 2:
        return None
    value = state.get("pending_clarification")
    if not isinstance(value, dict):
        return None
    if str(value.get("status") or "") not in _ACTIVE_CLARIFICATION_STATUSES:
        return None
    if not str(value.get("clarification_id") or ""):
        return None
    current_scope = _state_scope(state)
    if any(current_scope.values()):
        stored_scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
        if any(stored_scope.get(key) != expected for key, expected in current_scope.items()):
            return None
    return deepcopy(value)


def clarification_context_projection(state: dict[str, Any]) -> dict[str, Any] | None:
    blockers = active_goal_blockers(state)
    if blockers:
        return {
            "version": "goal-blocker-projection@1",
            "blockers": [
                {
                    "blocker_id": row.get("blocker_id"),
                    "goal_id": row.get("goal_id"),
                    "missing_kind": row.get("missing_kind"),
                    "question": row.get("question"),
                    "requested_effect": deepcopy(row.get("requested_effect"))
                    if isinstance(row.get("requested_effect"), dict)
                    else None,
                }
                for row in blockers
            ],
            "requires_single_global_disposition": False,
            "allowed_operations": [
                "RESOLVE_BLOCKER",
                "CANCEL_BLOCKER",
                "SUPERSEDE_BLOCKER",
            ],
            "authority": "model_proposes_goal_scoped_changes_runtime_verifies",
        }

    pending = active_pending_clarification(state)
    if pending is None:
        return None
    return {
        "version": pending.get("version"),
        "clarification_id": pending.get("clarification_id"),
        "status": pending.get("status"),
        "user_request": pending.get("user_request"),
        "question": pending.get("question"),
        "missing_kind": pending.get("missing_kind"),
        "attempt": pending.get("attempt"),
        "suspended_goals": [
            {
                "goal_id": row.get("goal_id"),
                "description": row.get("description"),
                "requested_effect": deepcopy(row.get("requested_effect"))
                if isinstance(row.get("requested_effect"), dict)
                else None,
                "goal_type": row.get("goal_type"),
                "required": row.get("required", True),
            }
            for row in list(pending.get("suspended_goals") or [])
            if isinstance(row, dict)
        ],
        "legacy_disposition_optional": ["resume", "abandon", "new_request"],
        "requires_single_global_disposition": False,
        "authority": "model_proposes_runtime_verifies",
    }


__all__ = [
    "active_goal_blockers",
    "active_pending_clarification",
    "clarification_context_projection",
    "goal_records_context_projection",
]
