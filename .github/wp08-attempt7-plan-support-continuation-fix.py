#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:180]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


workflow = ROOT / "services/agent-service/src/agent_core/lifecycle/workflow_runtime.py"
replace_once(
    workflow,
    '''    surface_by_goal = {
        str(row.get("goal_id") or ""): row
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }

    per_goal: dict[str, dict[str, Any]] = {}
''',
    '''    surface_by_goal = {
        str(row.get("goal_id") or ""): row
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }

    # A safe read can be an exact registered support effect for another still
    # pending Goal without semantically depending on that Goal or completing it.
    # This metadata only permits a bounded continuation; the later completion
    # call must still pass its own target, capability and transaction gates.
    support_continuation_by_goal: dict[str, dict[str, Any]] = {}
    support_safe_execution_kinds = {
        "observation",
        "grounding_read",
        "knowledge_read",
        "clarification_read",
    }
    if execution_kind in support_safe_execution_kinds:
        for continuation_goal_id, continuation_goal in declared_by_id.items():
            identity = canonical_effect_identity(continuation_goal.get("requested_effect"))
            surface_goal = surface_by_goal.get(continuation_goal_id, {})
            completion_tools = sorted({
                str(value)
                for value in list(surface_goal.get("completion_tools") or [])
                if str(value)
            })
            support_tools = {
                str(value)
                for value in list(surface_goal.get("support_tools") or [])
                if str(value)
            }
            eligible = bool(
                identity
                and identity in support_effect_identities
                and str(surface_goal.get("status") or "") == "exact_supported"
                and tool_name in support_tools
                and completion_tools
            )
            if eligible:
                support_continuation_by_goal[continuation_goal_id] = {
                    "requested_effect_identity": identity,
                    "support_tool": tool_name,
                    "completion_tools": completion_tools,
                    "source": "exact_registered_support_effect",
                    "safe_read_only": True,
                    "completes_goal": False,
                    "target_authority_granted": False,
                    "continuation_required": True,
                }

    per_goal: dict[str, dict[str, Any]] = {}
''',
)
replace_once(
    workflow,
    '''        "goal_effect_role": next(iter(set(roles.values()))) if roles and len(set(roles.values())) == 1 else "mixed" if roles else "none",
        "goal_completion_eligible": bool(completion_by_goal) and all(completion_by_goal.values()),
        "composite_goal_binding": len(goal_ids) > 1,
''',
    '''        "goal_effect_role": next(iter(set(roles.values()))) if roles and len(set(roles.values())) == 1 else "mixed" if roles else "none",
        "goal_completion_eligible": bool(completion_by_goal) and all(completion_by_goal.values()),
        "support_continuation_goal_ids": sorted(support_continuation_by_goal),
        "support_continuation_by_goal": support_continuation_by_goal,
        "composite_goal_binding": len(goal_ids) > 1,
''',
)
replace_once(
    workflow,
    '''                "goal_effect_roles": deepcopy(
                    (row.get("verification") or {}).get("goal_effect_roles")
                    if isinstance((row.get("verification") or {}).get("goal_effect_roles"), dict)
                    else {}
                ),
''',
    '''                "goal_effect_roles": deepcopy(
                    (row.get("verification") or {}).get("goal_effect_roles")
                    if isinstance((row.get("verification") or {}).get("goal_effect_roles"), dict)
                    else {}
                ),
                "support_continuation_goal_ids": [
                    str(value)
                    for value in list(
                        (row.get("verification") or {}).get("support_continuation_goal_ids") or []
                    )
                    if str(value)
                ],
''',
)
replace_once(
    workflow,
    '''        verification = row.get("verification") if isinstance(row.get("verification"), dict) else {}
        roles = verification.get("goal_effect_roles") if isinstance(verification.get("goal_effect_roles"), dict) else {}
        legacy_role = str(verification.get("goal_effect_role") or "")
        allowed_roles = {"completion", "support", "unsupported_report", "legacy_completion"}
        for goal_id in bound_goal_ids:
            role = str(roles.get(goal_id) or legacy_role)
            if role not in allowed_roles:
                errors.append(
                    {
                        "code": "PLAN_EXACT_CAPABILITY_ROLE_REQUIRED",
                        "effect_id": effect_id,
                        "goal_id": goal_id,
                        "role": role or None,
                    }
                )
''',
    '''        verification = row.get("verification") if isinstance(row.get("verification"), dict) else {}
        roles = verification.get("goal_effect_roles") if isinstance(verification.get("goal_effect_roles"), dict) else {}
        legacy_role = str(verification.get("goal_effect_role") or "")
        allowed_roles = {"completion", "support", "unsupported_report", "legacy_completion"}
        for goal_id in bound_goal_ids:
            role = str(roles.get(goal_id) or legacy_role)
            if role not in allowed_roles:
                errors.append(
                    {
                        "code": "PLAN_EXACT_CAPABILITY_ROLE_REQUIRED",
                        "effect_id": effect_id,
                        "goal_id": goal_id,
                        "role": role or None,
                    }
                )
        continuation_goal_ids = [
            str(value)
            for value in list(verification.get("support_continuation_goal_ids") or [])
            if str(value)
        ]
        unknown_continuations = [
            goal_id for goal_id in continuation_goal_ids if goal_id not in known_goal_ids
        ]
        if unknown_continuations:
            errors.append(
                {
                    "code": "PLAN_UNKNOWN_SUPPORT_CONTINUATION_GOAL",
                    "effect_id": effect_id,
                    "goal_ids": unknown_continuations,
                }
            )
''',
)
old_uncovered = '''    uncovered_required_goals = [
        str(row.get("goal_id") or "")
        for row in goals
        if bool(row.get("required", True))
        and str(row.get("coverage_status") or "") == GoalCoverageStatus.PENDING.value
        and not any(
            str(row.get("goal_id") or "") in {str(value) for value in list(step.get("goal_ids") or []) if str(value)}
            and str(
                ((step.get("verification") or {}).get("goal_effect_roles") or {}).get(str(row.get("goal_id") or ""))
                if isinstance((step.get("verification") or {}).get("goal_effect_roles"), dict)
                else (step.get("verification") or {}).get("goal_effect_role") or ""
            ) in ({"completion", "unsupported_report"} | ({"legacy_completion"} if not semantic else set()))
            for step in steps
        )
    ]
    if uncovered_required_goals:
        errors.append(
            {
                "code": "PLAN_REQUIRED_GOAL_HAS_NO_COMPLETION_PATH",
                "goal_ids": uncovered_required_goals,
            }
        )
'''
new_uncovered = '''    def _step_role_for_goal(step: dict[str, Any], goal_id: str) -> str:
        verification = step.get("verification") if isinstance(step.get("verification"), dict) else {}
        roles = verification.get("goal_effect_roles") if isinstance(verification.get("goal_effect_roles"), dict) else {}
        return str(roles.get(goal_id) or verification.get("goal_effect_role") or "")

    def _has_current_completion(goal_id: str) -> bool:
        allowed_completion_roles = {"completion", "unsupported_report"} | (
            {"legacy_completion"} if not semantic else set()
        )
        return any(
            goal_id in {str(value) for value in list(step.get("goal_ids") or []) if str(value)}
            and _step_role_for_goal(step, goal_id) in allowed_completion_roles
            for step in steps
        )

    def _has_exact_support_continuation(goal_id: str) -> bool:
        return any(
            goal_id in {
                str(value)
                for value in list(
                    ((step.get("verification") or {}).get("support_continuation_goal_ids") or [])
                    if isinstance(step.get("verification"), dict)
                    else []
                )
                if str(value)
            }
            for step in steps
        )

    pending_required_goal_ids = [
        str(row.get("goal_id") or "")
        for row in goals
        if bool(row.get("required", True))
        and str(row.get("coverage_status") or "") == GoalCoverageStatus.PENDING.value
        and str(row.get("goal_id") or "")
    ]
    support_continuation_goal_ids = [
        goal_id
        for goal_id in pending_required_goal_ids
        if not _has_current_completion(goal_id)
        and _has_exact_support_continuation(goal_id)
    ]
    uncovered_required_goals = [
        goal_id
        for goal_id in pending_required_goal_ids
        if not _has_current_completion(goal_id)
        and goal_id not in set(support_continuation_goal_ids)
    ]
    if support_continuation_goal_ids:
        warnings.append(
            {
                "code": "PLAN_REQUIRED_GOAL_DEFERRED_BY_EXACT_SUPPORT",
                "goal_ids": support_continuation_goal_ids,
                "goal_remains_incomplete": True,
                "completion_required_on_continuation": True,
                "target_authority_granted": False,
            }
        )
    if uncovered_required_goals:
        errors.append(
            {
                "code": "PLAN_REQUIRED_GOAL_HAS_NO_COMPLETION_PATH",
                "goal_ids": uncovered_required_goals,
            }
        )
'''
replace_once(workflow, old_uncovered, new_uncovered)

policy = ROOT / "services/agent-service/src/agent_core/lifecycle/pretool_execution_policy.py"
replace_once(
    policy,
    '''        frontier = sorted({tool for row in active_paths for tool in list(row.get("frontier") or [])})
        allowed_tools.update(frontier)
        all_active_complete = bool(active_paths) and all(bool(row.get("path_complete")) for row in active_paths)
        goal_policies.append({
''',
    '''        frontier = sorted({tool for row in active_paths for tool in list(row.get("frontier") or [])})
        all_active_complete = bool(active_paths) and all(bool(row.get("path_complete")) for row in active_paths)

        # Action-draft completion may require an authoritative target even when
        # the Contract-v2 target binding can theoretically come from an external
        # resolver.  Exact registered read-support tools are therefore exposed
        # as an optional bounded frontier before the draft.  They do not satisfy
        # the Goal, do not grant target authority, and disappear for this Goal
        # after their PlanRun step succeeds.
        active_completion_tools = {
            str(row.get("completion_tool") or "")
            for row in active_paths
            if str(row.get("completion_tool") or "")
        }
        action_completion_pending = bool(
            not all_active_complete
            and any(
                (contract := capability_registry.contract_for_tool(name)) is not None
                and str(contract.execution_kind or "") == "action_draft"
                for name in active_completion_tools
            )
        )
        support_frontier: list[str] = []
        if action_completion_pending:
            for name in list(decision.get("support_tools") or []):
                tool_name = str(name or "")
                support_contract = capability_registry.contract_for_tool(tool_name)
                if (
                    tool_name
                    and tool_name not in completed_tools
                    and support_contract is not None
                    and str(support_contract.execution_kind or "")
                    in {"observation", "grounding_read", "knowledge_read", "clarification_read"}
                ):
                    support_frontier.append(tool_name)
        support_frontier = sorted(set(support_frontier))
        frontier = sorted(set(frontier) | set(support_frontier))
        allowed_tools.update(frontier)
        goal_policies.append({
''',
)
replace_once(
    policy,
    '''            "allowed_tools": frontier,
            "active_path_ids": [str(row.get("path_id") or "") for row in active_paths],
            "candidate_path_count": len(closed_paths),
            "max_path_progress": max_progress,
            "reason": "highest_progress_contract_paths_only",
''',
    '''            "allowed_tools": frontier,
            "support_frontier_tools": support_frontier,
            "support_frontier_is_completion": False,
            "active_path_ids": [str(row.get("path_id") or "") for row in active_paths],
            "candidate_path_count": len(closed_paths),
            "max_path_progress": max_progress,
            "reason": (
                "highest_progress_contract_paths_plus_exact_action_support"
                if support_frontier
                else "highest_progress_contract_paths_only"
            ),
''',
)

print("attempt7 exact support-continuation planning repair applied")
