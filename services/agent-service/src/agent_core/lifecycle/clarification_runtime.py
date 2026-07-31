from __future__ import annotations

"""Durable, domain-neutral clarification continuation protocol.

Clarification is not a completed user goal.  It pauses one or more already
declared goals until the user supplies the missing target, scope, condition or
intent.  This module persists only orchestration evidence: it never selects a
business object, invents a form value, or grants an execution permit.
"""

from copy import deepcopy
from typing import Any
from uuid import uuid4

from agent_core.context.state_projection import (
    active_pending_clarification,
    clarification_context_projection,
)
from agent_core.kernel.plan_projection_contract import read_plan_projection
from agent_core.kernel.state_schema_contract import legacy_fallback_allowed
from agent_core.lifecycle.goal_blockers import active_goal_blockers, merge_goal_blockers
from agent_core.lifecycle.semantic_contract import semantic_goals


PENDING_CLARIFICATION_VERSION = "pending-clarification@1"


def _state_scope(state: dict[str, Any]) -> dict[str, str]:
    return {
        "tenant_id": str(state.get("current_tenant_id") or ""),
        "user_id": str(state.get("current_user_id") or ""),
        "thread_id": str(state.get("current_thread_id") or ""),
    }


def _surface_candidates(surface: dict[str, Any] | None) -> dict[str, list[str]]:
    rows = list((surface or {}).get("goals") or []) if isinstance(surface, dict) else []
    return {
        str(row.get("goal_id") or ""): list(dict.fromkeys(
            str(name) for name in list(row.get("candidate_tools") or []) if str(name)
        ))
        for row in rows
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }


def suspend_for_clarification(
    *,
    state: dict[str, Any],
    call: dict[str, Any],
    capability_surface: dict[str, Any] | None,
) -> dict[str, Any]:
    """Create or update one durable clarification checkpoint.

    Capability names are copied only from the registry-produced surface.  On a
    later turn they merely keep the same capability eligible for discovery;
    the current reply must still resolve a target and pass CapabilityGate.
    """

    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    bound_goal_ids = list(dict.fromkeys(
        str(value) for value in list(args.get("goal_ids") or []) if str(value)
    ))
    candidates = _surface_candidates(capability_surface)
    prior = active_pending_clarification(state)
    formal_contract = state.get("frozen_semantic_contract") if isinstance(state.get("frozen_semantic_contract"), dict) else {}
    formal_goals = semantic_goals(state)
    turn_plan = (
        state.get("turn_goal_plan")
        if legacy_fallback_allowed(state) and isinstance(state.get("turn_goal_plan"), dict)
        else {}
    )
    workflow = read_plan_projection(state) or {}
    suspended_domain_goal_ids = {
        str(row.get("goal_id") or "")
        for row in list(workflow.get("goals") or [])
        if isinstance(row, dict)
        and bool(row.get("required", True))
        and str(row.get("coverage_status") or "") in {"PENDING", "BLOCKED"}
        and str(row.get("goal_type") or "") not in {"clarification", "narrative"}
    }
    selected_goal_ids = suspended_domain_goal_ids or set(bound_goal_ids)
    source_goals = formal_goals or [
        row for row in list(turn_plan.get("goals") or []) if isinstance(row, dict)
    ]
    current_goals = [
        row for row in source_goals
        if isinstance(row, dict) and str(row.get("goal_id") or "") in selected_goal_ids
    ]
    candidates = _surface_candidates(capability_surface)

    if prior is not None:
        suspended_goals = list(prior.get("suspended_goals") or [])
        resume_map = {
            str(key): str(value)
            for key, value in dict(prior.get("resume_goal_map") or {}).items()
            if str(key) and str(value)
        }
        by_root = {
            str(row.get("goal_id") or ""): deepcopy(row)
            for row in suspended_goals if isinstance(row, dict)
        }
        for goal in current_goals:
            current_id = str(goal.get("goal_id") or "")
            root_id = resume_map.get(current_id) or str(goal.get("continuation_of") or "")
            if root_id in by_root and candidates.get(current_id):
                by_root[root_id]["completion_tool_names"] = list(dict.fromkeys([
                    *list(by_root[root_id].get("completion_tool_names") or []),
                    *candidates[current_id],
                ]))
        suspended_goals = list(by_root.values())
        clarification_id = str(prior.get("clarification_id") or "")
        created_turn = int(prior.get("created_turn") or state.get("turn_index") or 0)
        user_request = str(prior.get("user_request") or "")
        attempt = int(prior.get("attempt") or 1) + 1
    else:
        suspended_goals = [
            {
                "goal_id": str(goal.get("goal_id") or ""),
                "description": str(goal.get("description") or ""),
                "goal_type": str(goal.get("goal_type") or ""),
                "requested_effect": deepcopy(goal.get("requested_effect")) if isinstance(goal.get("requested_effect"), dict) else None,
                "required": bool(goal.get("required", True)),
                "completion_tool_names": candidates.get(str(goal.get("goal_id") or ""), []),
            }
            for goal in current_goals
        ]
        clarification_id = f"clarification:{uuid4().hex}"
        created_turn = int(state.get("turn_index") or 0)
        user_request = str(formal_contract.get("user_text") or state.get("current_user_input") or "")
        attempt = 1

    return {
        "version": PENDING_CLARIFICATION_VERSION,
        "clarification_id": clarification_id,
        "status": "pending",
        "created_turn": created_turn,
        "updated_turn": int(state.get("turn_index") or 0),
        "attempt": attempt,
        "scope": _state_scope(state),
        "user_request": user_request,
        "question": str(args.get("question") or ""),
        "reason": str(args.get("reason") or ""),
        "missing_kind": str(args.get("missing_kind") or ""),
        "evidence_handles": list(dict.fromkeys(
            str(value) for value in list(args.get("evidence_handles") or []) if str(value)
        )),
        "suspended_goals": suspended_goals,
        "resume_goal_map": {},
        "authority": "orchestration_only_not_business_fact_or_target",
        "runtime_auto_select_target": False,
        "runtime_auto_switch_capability": False,
    }


def goal_blockers_for_clarification(
    *,
    state: dict[str, Any],
    call: dict[str, Any],
    capability_surface: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Create/update independent blockers for each goal bound to clarification."""
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    bound_goal_ids = list(dict.fromkeys(
        str(value) for value in list(args.get("goal_ids") or []) if str(value)
    ))
    candidates = _surface_candidates(capability_surface)
    formal_goals = semantic_goals(state)
    plan: dict[str, Any] = {}
    workflow = read_plan_projection(state) or {}
    pending_ids = {
        str(row.get("goal_id") or "")
        for row in list(workflow.get("goals") or [])
        if isinstance(row, dict)
        and bool(row.get("required", True))
        and str(row.get("coverage_status") or "") in {"PENDING", "BLOCKED"}
    }
    selected = pending_ids or set(bound_goal_ids)
    source_goals = formal_goals or [
        row for row in list(plan.get("goals") or []) if isinstance(row, dict)
    ]
    goals = [
        row for row in source_goals
        if isinstance(row, dict) and str(row.get("goal_id") or "") in selected
    ]
    turn = int(state.get("turn_index") or 0)
    additions = []
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
            "requested_effect": deepcopy(goal.get("requested_effect")) if isinstance(goal.get("requested_effect"), dict) else None,
            "source_user_request": str((state.get("frozen_semantic_contract") or {}).get("user_text") or state.get("current_user_input") or ""),
            "completion_tool_names": candidates.get(goal_id, []),
            "evidence_handles": list(dict.fromkeys(
                str(value) for value in list(args.get("evidence_handles") or []) if str(value)
            )),
            "authority": "goal_scoped_orchestration_blocker_not_business_fact",
        })
    return merge_goal_blockers(state.get("goal_blockers") or [], additions)


def transition_after_goal_declaration(
    pending: dict[str, Any] | None,
    goal_plan: dict[str, Any],
) -> dict[str, Any] | None:
    """Apply a validated planner disposition to the durable checkpoint."""

    if not isinstance(pending, dict):
        return None
    resolution = goal_plan.get("clarification_resolution")
    if not isinstance(resolution, dict):
        return deepcopy(pending)
    disposition = str(resolution.get("disposition") or "")
    if disposition in {"abandon", "new_request"}:
        return None
    if disposition != "resume":
        return deepcopy(pending)
    mapping = {
        str(goal.get("goal_id") or ""): str(goal.get("continuation_of") or "")
        for goal in list(goal_plan.get("goals") or [])
        if isinstance(goal, dict)
        and str(goal.get("goal_id") or "")
        and str(goal.get("continuation_of") or "")
    }
    return {
        **deepcopy(pending),
        "status": "resuming",
        "resume_turn": int(goal_plan.get("turn") or 0),
        "resume_goal_map": mapping,
        "reply_evidence_span": str(resolution.get("evidence_span") or ""),
    }


def continuation_tool_hints(
    state: dict[str, Any],
    goals: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Return capability hints from explicit Goal continuation relations.

    State Schema v2 reads only goal-scoped blockers. Legacy pending clarification
    is consulted only for an unmigrated schema-v1 checkpoint.
    """
    blockers = active_goal_blockers(state)
    root_tools = {
        str(row.get("goal_id") or ""): list(dict.fromkeys(
            str(name) for name in list(row.get("completion_tool_names") or []) if str(name)
        ))
        for row in blockers
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
    if hints or int(state.get("state_schema_version") or 1) >= 2:
        return hints

    pending = active_pending_clarification(state)
    if pending is None or str(pending.get("status") or "") != "resuming":
        return {}
    legacy_root_tools = {
        str(row.get("goal_id") or ""): list(dict.fromkeys(
            str(name) for name in list(row.get("completion_tool_names") or []) if str(name)
        ))
        for row in list(pending.get("suspended_goals") or [])
        if isinstance(row, dict)
    }
    mapping = {
        str(key): str(value)
        for key, value in dict(pending.get("resume_goal_map") or {}).items()
        if str(key) and str(value)
    }
    return {
        goal_id: legacy_root_tools.get(mapping.get(goal_id, ""), [])
        for goal in goals
        if isinstance(goal, dict)
        and (goal_id := str(goal.get("goal_id") or "")) in mapping
        and legacy_root_tools.get(mapping[goal_id])
    }


__all__ = [
    "PENDING_CLARIFICATION_VERSION",
    "active_pending_clarification",
    "clarification_context_projection",
    "continuation_tool_hints",
    "goal_blockers_for_clarification",
    "suspend_for_clarification",
    "transition_after_goal_declaration",
]
