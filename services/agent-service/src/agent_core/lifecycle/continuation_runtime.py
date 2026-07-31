from __future__ import annotations

"""Explicit continuation projection with no open-language heuristics."""

from typing import Any

from agent_core.lifecycle.clarification_runtime import continuation_tool_hints


def verified_continuation_tool_hints(
    state: dict[str, Any],
    goals: list[dict[str, Any]],
    capability_registry: Any,
) -> dict[str, list[str]]:
    """Return capability hints only from explicit, verified goal relations.

    Runtime never scans the user text for pronouns, correction phrases or
    ellipsis. A prior capability remains eligible only when the frozen goal
    explicitly names ``continuation_of`` or a verified legacy clarification
    checkpoint names the mapping. CapabilityGate still performs exact matching.
    """
    records = {
        str(row.get("goal_id") or ""): row
        for row in list(state.get("goal_records") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }
    hints: dict[str, list[str]] = {}
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        goal_id = str(goal.get("goal_id") or "")
        continuation_of = str(goal.get("continuation_of") or "")
        prior = records.get(continuation_of) if continuation_of else None
        names = [
            str(name)
            for name in list((prior or {}).get("completion_tool_names") or [])
            if str(name) and capability_registry.contract_for_tool(str(name)) is not None
        ]
        if goal_id and names:
            hints[goal_id] = list(dict.fromkeys(names))
    for goal_id, names in continuation_tool_hints(state, goals).items():
        key = str(goal_id)
        hints[key] = list(dict.fromkeys([
            *hints.get(key, []),
            *(str(name) for name in names if str(name)),
        ]))
    return hints


__all__ = ["verified_continuation_tool_hints"]
