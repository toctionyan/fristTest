from __future__ import annotations

"""Goal-scoped clarification blockers for State Schema v2.

Legacy singleton clarification checkpoints are migrated by ``state_schema``.
Current-turn runtime persists only independent GoalBlockers.
"""

from copy import deepcopy
from typing import Any

from agent_core.context.state_projection import clarification_context_projection
from agent_core.kernel.plan_projection_contract import read_plan_projection
from agent_core.lifecycle.goal_blockers import active_goal_blockers, merge_goal_blockers
from agent_core.lifecycle.semantic_contract import semantic_goals


def _surface_candidates(surface: dict[str, Any] | None) -> dict[str, list[str]]:
    rows = list((surface or {}).get("goals") or []) if isinstance(surface, dict) else []
    return {
        str(row.get("goal_id") or ""): list(dict.fromkeys(
            str(name) for name in list(row.get("candidate_tools") or []) if str(name)
        ))
        for row in rows
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }


def goal_blockers_for_clarification(
    *,
    state: dict[str, Any],
    call: dict[str, Any],
    capability_surface: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Create/update independent blockers for each formally declared Goal."""
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    bound_goal_ids = list(dict.fromkeys(
        str(value) for value in list(args.get("goal_ids") or []) if str(value)
    ))
    candidates = _surface_candidates(capability_surface)
    formal_goals = semantic_goals(state)
    workflow = read_plan_projection(state) or {}
    pending_ids = {
        str(row.get("goal_id") or "")
        for row in list(workflow.get("goals") or [])
        if isinstance(row, dict)
        and bool(row.get("required", True))
        and str(row.get("coverage_status") or "") in {"PENDING", "BLOCKED"}
    }
    selected = pending_ids or set(bound_goal_ids)
    goals = [
        row for row in formal_goals
        if isinstance(row, dict) and str(row.get("goal_id") or "") in selected
    ]
    turn = int(state.get("turn_index") or 0)
    additions: list[dict[str, Any]] = []
    for goal in goals:
        goal_id = str(goal.get("goal_id") or "")
        additions.append({
            "blocker_id": f"blocker:{goal_id}:{str(args.get('missing_kind') or 'input')}",
            "goal_id": goal_id,
            "status": "OPEN",
            "missing_kind": str(args.get("missing_kind") or "condition"),
            "question": str(args.get("question") or ""),
            "reason": str(args.get("reason") or ""),
            "created_turn": turn,
            "updated_turn": turn,
            "requested_effect": deepcopy(goal.get("requested_effect"))
            if isinstance(goal.get("requested_effect"), dict) else None,
            "source_user_request": str(
                (state.get("frozen_semantic_contract") or {}).get("user_text")
                or state.get("current_user_input")
                or ""
            ),
            "completion_tool_names": candidates.get(goal_id, []),
            "evidence_handles": list(dict.fromkeys(
                str(value) for value in list(args.get("evidence_handles") or []) if str(value)
            )),
            "authority": "goal_scoped_orchestration_blocker_not_business_fact",
        })
    return merge_goal_blockers(state.get("goal_blockers") or [], additions)


def continuation_tool_hints(
    state: dict[str, Any],
    goals: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Return capability hints only from explicit Goal continuation relations."""
    root_tools = {
        str(row.get("goal_id") or ""): list(dict.fromkeys(
            str(name) for name in list(row.get("completion_tool_names") or []) if str(name)
        ))
        for row in active_goal_blockers(state)
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }
    hints: dict[str, list[str]] = {}
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        goal_id = str(goal.get("goal_id") or "")
        continuation_of = str(goal.get("continuation_of") or "")
        names = root_tools.get(continuation_of, [])
        if goal_id and names:
            hints[goal_id] = list(names)
    return hints


__all__ = [
    "clarification_context_projection",
    "continuation_tool_hints",
    "goal_blockers_for_clarification",
]
