from __future__ import annotations

"""Goal-scoped blockers replacing the single global clarification disposition."""

from copy import deepcopy
from typing import Any, Iterable

from agent_core.context.state_projection import active_goal_blockers

_VALID_OPERATIONS = {"RESOLVE_BLOCKER", "CANCEL_BLOCKER", "SUPERSEDE_BLOCKER"}


def apply_blocker_resolutions(
    blockers: Iterable[dict[str, Any]],
    resolutions: Iterable[dict[str, Any]],
    *,
    turn: int,
) -> list[dict[str, Any]]:
    result = [deepcopy(row) for row in list(blockers or []) if isinstance(row, dict)]
    by_id = {str(row.get("blocker_id") or ""): row for row in result if str(row.get("blocker_id") or "")}
    seen: set[str] = set()
    for resolution in list(resolutions or []):
        if not isinstance(resolution, dict):
            continue
        blocker_id = str(resolution.get("blocker_id") or "").strip()
        operation = str(resolution.get("operation") or "").strip().upper()
        if not blocker_id or blocker_id in seen:
            raise ValueError(f"invalid_or_duplicate_blocker_resolution:{blocker_id or 'missing'}")
        seen.add(blocker_id)
        blocker = by_id.get(blocker_id)
        if blocker is None:
            raise ValueError(f"unknown_blocker:{blocker_id}")
        if str(blocker.get("status") or "OPEN").upper() != "OPEN":
            raise ValueError(f"blocker_not_open:{blocker_id}")
        if operation not in _VALID_OPERATIONS:
            raise ValueError(f"invalid_blocker_operation:{blocker_id}:{operation}")
        blocker["status"] = {
            "RESOLVE_BLOCKER": "RESOLVED",
            "CANCEL_BLOCKER": "CANCELLED",
            "SUPERSEDE_BLOCKER": "SUPERSEDED",
        }[operation]
        blocker["resolved_turn"] = int(turn)
        blocker["resolution_operation"] = operation
        blocker["resolution_evidence_span"] = str(resolution.get("evidence_span") or "").strip()
        if resolution.get("value") is not None:
            blocker["resolution_value"] = deepcopy(resolution.get("value"))
    return result


def merge_goal_blockers(
    existing: Iterable[dict[str, Any]],
    additions: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [deepcopy(row) for row in list(existing or []) if isinstance(row, dict)]
    positions = {str(row.get("blocker_id") or ""): index for index, row in enumerate(rows)}
    for addition in list(additions or []):
        if not isinstance(addition, dict):
            continue
        blocker_id = str(addition.get("blocker_id") or "").strip()
        goal_id = str(addition.get("goal_id") or "").strip()
        if not blocker_id or not goal_id:
            raise ValueError("blocker_id_and_goal_id_required")
        normalized = deepcopy(addition)
        normalized.setdefault("status", "OPEN")
        if blocker_id in positions:
            rows[positions[blocker_id]] = normalized
        else:
            positions[blocker_id] = len(rows)
            rows.append(normalized)
    return rows
