from __future__ import annotations

"""Canonical read-only lifecycle projections used by ContextBundle.

State Schema v2 exposes Goal, Blocker and Focus projections only. Legacy
checkpoint fields are consumed exclusively by ``lifecycle.state_schema`` before
this module is called.
"""

from copy import deepcopy
from typing import Any, Iterable

_ACTIVE_GOAL_LIFECYCLES = {"OPEN", "ACTIVE", "BLOCKED", "PAUSED"}
_ACTIVE_BLOCKER_STATUSES = {"OPEN"}


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
            "resolved_reference": deepcopy(row.get("resolved_reference"))
            if isinstance(row.get("resolved_reference"), dict)
            else None,
            "completion_tool_names": list(row.get("completion_tool_names") or []),
            "updated_turn": row.get("updated_turn"),
            "authority": "semantic_goal_lifecycle_not_business_fact",
        }
        for row in rows[: max(0, int(limit))]
    ]


def clarification_context_projection(state: dict[str, Any]) -> dict[str, Any] | None:
    blockers = active_goal_blockers(state)
    if not blockers:
        return None
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


__all__ = [
    "active_goal_blockers",
    "clarification_context_projection",
    "goal_records_context_projection",
]
