from __future__ import annotations

"""Compile the pre-tool capability frontier used by the model call.

This module is deliberately narrower than the formal workflow runtime:

* FrozenSemanticContract owns user goals.
* Capability Contract v2 owns capability topology.
* This policy owns only which registered business capabilities may be exposed
  on the *next* model call.
* MatchProof, ExecutionPermit, target resolution, transaction authority and
  Business Service remain downstream authorities.

The policy never invents arguments, business facts, goal semantics or permits.
It converts the existing pre-tool contract plan into a bounded provider tool
surface and records explicit migration fallbacks for capabilities that have not
reached Contract v2 yet.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.lifecycle.plan_execution import (
    validate_frozen_plan_definition,
    validate_plan_run,
)
from agent_core.lifecycle.pretool_planner import build_pretool_shadow_plan
from agent_core.lifecycle.goal_outputs import reusable_goal_outputs_for_goal

PRETOOL_EXECUTION_POLICY_VERSION = "pretool-execution-policy@1"
_PROGRESS_STEP_STATES = {"SUCCEEDED"}
_COMPLETED_GOAL_LIFECYCLES = {"COMPLETED"}


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _completed_goal_ids(state: dict[str, Any]) -> set[str]:
    """Read dependency completion only from the durable Goal lifecycle authority.

    PlanRun ``terminal_goal_states`` is a dialogue/execution projection.  It is
    intentionally not used to unlock another goal because ``validate_plan_run``
    proves structural compatibility, not the semantic integrity of arbitrary
    terminal-state mutations.
    """

    return {
        str(row.get("goal_id") or "")
        for row in list(state.get("goal_records") or [])
        if isinstance(row, dict)
        and str(row.get("lifecycle") or "").upper() in _COMPLETED_GOAL_LIFECYCLES
        and str(row.get("goal_id") or "")
    }


def _completed_tools_by_goal(state: dict[str, Any]) -> tuple[dict[str, set[str]], list[str]]:
    """Read only integrity-checked PlanRun evidence from the preceding loop."""

    definition = state.get("frozen_plan_definition")
    run = state.get("plan_run")
    if not isinstance(definition, dict) or not isinstance(run, dict):
        return {}, []

    definition_check = validate_frozen_plan_definition(definition)
    if not definition_check.get("ok"):
        return {}, [str(definition_check.get("code") or "FROZEN_PLAN_DEFINITION_INVALID")]
    run_check = validate_plan_run(definition=definition, plan_run=run)
    if not run_check.get("ok"):
        return {}, [str(run_check.get("code") or "PLAN_RUN_INVALID")]

    completed: dict[str, set[str]] = {}
    step_states = run.get("step_states") if isinstance(run.get("step_states"), dict) else {}
    for step in list(definition.get("steps") or []):
        if not isinstance(step, dict):
            continue
        effect_id = str(step.get("effect_id") or "")
        tool_name = str(step.get("tool_name") or "")
        step_state = step_states.get(effect_id) if isinstance(step_states.get(effect_id), dict) else {}
        if not effect_id or not tool_name or str(step_state.get("status") or "") not in _PROGRESS_STEP_STATES:
            continue
        for goal_id in [str(value) for value in list(step.get("goal_ids") or []) if str(value)]:
            completed.setdefault(goal_id, set()).add(tool_name)
    return completed, []


def _path_frontier(
    path: dict[str, Any],
    *,
    completed_tools: set[str],
) -> tuple[list[str], int, bool]:
    steps = [row for row in list(path.get("steps") or []) if isinstance(row, dict)]
    tool_by_step_id = {
        str(row.get("step_id") or ""): str(row.get("tool_name") or "")
        for row in steps
        if str(row.get("step_id") or "") and str(row.get("tool_name") or "")
    }
    progress = sum(
        1 for row in steps if str(row.get("tool_name") or "") in completed_tools
    )
    for step in steps:
        tool_name = str(step.get("tool_name") or "")
        if not tool_name or tool_name in completed_tools:
            continue
        dependency_tools = {
            tool_by_step_id.get(str(step_id), "")
            for step_id in list(step.get("depends_on_step_ids") or [])
        }
        dependency_tools.discard("")
        if dependency_tools <= completed_tools:
            return [tool_name], progress, False
        return [], progress, False
    return [], progress, True


def _unsupported_tools_for_goal(
    *,
    registry: CapabilityRegistry,
    surface_decision: dict[str, Any],
) -> list[str]:
    result: list[str] = []
    for tool_name in list(surface_decision.get("candidate_tools") or []):
        name = str(tool_name or "")
        contract = registry.contract_for_tool(name)
        if contract is not None and str(contract.execution_kind or "") == "unsupported":
            result.append(name)
    return sorted(set(result))


def _legacy_exact_tools(surface_decision: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(value)
            for value in list(surface_decision.get("candidate_tools") or [])
            if str(value)
        }
    )


def build_pretool_execution_policy(
    *,
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
    shadow_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the provider capability frontier for the next model call.

    Contract-v2 goals are enforced by topology and prior PlanRun progress.
    Non-v2 goals retain the already exact effect surface as an explicit,
    auditable migration fallback; they are never silently presented as fully
    governed.
    """

    plan = deepcopy(shadow_plan) if isinstance(shadow_plan, dict) else build_pretool_shadow_plan(
        state=state,
        capability_registry=capability_registry,
    )
    surface = plan.get("capability_surface") if isinstance(plan.get("capability_surface"), dict) else {}
    surface_by_goal = {
        str(row.get("goal_id") or ""): row
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }
    completed_tools_by_goal, evidence_errors = _completed_tools_by_goal(state)
    completed_goal_ids = _completed_goal_ids(state)

    goal_policies: list[dict[str, Any]] = []
    allowed_tools: set[str] = set()
    migration_gap_goal_ids: list[str] = []
    dependency_blocked_goal_ids: list[str] = []

    for goal_plan in list(plan.get("goal_plans") or []):
        if not isinstance(goal_plan, dict):
            continue
        goal_id = str(goal_plan.get("goal_id") or "")
        decision = surface_by_goal.get(goal_id, {})
        dependencies = {
            str(value)
            for value in list(goal_plan.get("depends_on_goal_ids") or [])
            if str(value)
        }
        completed_tools = set(completed_tools_by_goal.get(goal_id, set()))
        reusable_by_tool, reusable_outputs, output_evidence_errors = reusable_goal_outputs_for_goal(
            state=state,
            capability_registry=capability_registry,
            goal_plan=goal_plan,
            dependency_goal_ids=dependencies,
        )
        completed_tools.update(reusable_by_tool)
        reusable_output_ids = sorted({
            str(row.get("goal_output_ref_id") or "")
            for row in reusable_outputs
            if str(row.get("goal_output_ref_id") or "")
        })
        surface_status = str(decision.get("status") or goal_plan.get("surface_status") or "")

        if goal_id in completed_goal_ids:
            row = {
                "goal_id": goal_id,
                "status": "GOAL_ALREADY_COMPLETED",
                "enforcement": "contract_frontier",
                "depends_on_goal_ids": sorted(dependencies),
                "completed_tools": sorted(completed_tools),
                "reused_goal_output_ref_ids": reusable_output_ids,
                "goal_output_evidence_errors": output_evidence_errors,
                "allowed_tools": [],
                "active_path_ids": [],
                "reason": "goal_lifecycle_completed",
            }
            goal_policies.append(row)
            continue

        missing_dependencies = sorted(dependencies - completed_goal_ids)
        if missing_dependencies:
            dependency_blocked_goal_ids.append(goal_id)
            goal_policies.append({
                "goal_id": goal_id,
                "status": "BLOCKED_BY_GOAL_DEPENDENCY",
                "enforcement": "contract_frontier",
                "depends_on_goal_ids": sorted(dependencies),
                "missing_dependency_goal_ids": missing_dependencies,
                "completed_tools": sorted(completed_tools),
                "reused_goal_output_ref_ids": reusable_output_ids,
                "goal_output_evidence_errors": output_evidence_errors,
                "allowed_tools": [],
                "active_path_ids": [],
                "reason": "declared_goal_dependency_not_completed",
            })
            continue

        if surface_status in {"absent_proven", "completion_capability_absent"}:
            unsupported = _unsupported_tools_for_goal(
                registry=capability_registry,
                surface_decision=decision,
            )
            allowed_tools.update(unsupported)
            goal_policies.append({
                "goal_id": goal_id,
                "status": "UNSUPPORTED_EXACTLY_PROVEN",
                "enforcement": "exact_absence_report",
                "depends_on_goal_ids": sorted(dependencies),
                "completed_tools": sorted(completed_tools),
                "reused_goal_output_ref_ids": reusable_output_ids,
                "goal_output_evidence_errors": output_evidence_errors,
                "allowed_tools": unsupported,
                "active_path_ids": [],
                "reason": "no_exact_completion_capability",
            })
            continue

        closed_paths = [
            row
            for row in list(goal_plan.get("candidate_paths") or [])
            if isinstance(row, dict) and str(row.get("status") or "") == "closed"
        ]
        if not closed_paths:
            fallback = _legacy_exact_tools(decision)
            migration_gap_goal_ids.append(goal_id)
            allowed_tools.update(fallback)
            goal_policies.append({
                "goal_id": goal_id,
                "status": "CONTRACT_V2_MIGRATION_GAP",
                "enforcement": "legacy_exact_surface_fallback",
                "depends_on_goal_ids": sorted(dependencies),
                "completed_tools": sorted(completed_tools),
                "reused_goal_output_ref_ids": reusable_output_ids,
                "goal_output_evidence_errors": output_evidence_errors,
                "allowed_tools": fallback,
                "active_path_ids": [],
                "reason": "no_contract_closed_path",
            })
            continue

        evaluated: list[dict[str, Any]] = []
        for path in closed_paths:
            frontier, progress, complete = _path_frontier(
                path,
                completed_tools=completed_tools,
            )
            evaluated.append({
                "path_id": str(path.get("path_id") or ""),
                "completion_tool": str(path.get("completion_tool") or ""),
                "progress": progress,
                "frontier": frontier,
                "path_complete": complete,
            })
        max_progress = max((int(row.get("progress") or 0) for row in evaluated), default=0)
        active_paths = [row for row in evaluated if int(row.get("progress") or 0) == max_progress]
        frontier = sorted({tool for row in active_paths for tool in list(row.get("frontier") or [])})
        allowed_tools.update(frontier)
        all_active_complete = bool(active_paths) and all(bool(row.get("path_complete")) for row in active_paths)
        goal_policies.append({
            "goal_id": goal_id,
            "status": "PATH_COMPLETE_AWAITING_TERMINAL" if all_active_complete else "FRONTIER_READY",
            "enforcement": "contract_frontier",
            "depends_on_goal_ids": sorted(dependencies),
            "completed_tools": sorted(completed_tools),
            "reused_goal_output_ref_ids": reusable_output_ids,
            "goal_output_evidence_errors": output_evidence_errors,
            "allowed_tools": frontier,
            "active_path_ids": [str(row.get("path_id") or "") for row in active_paths],
            "candidate_path_count": len(closed_paths),
            "max_path_progress": max_progress,
            "reason": "highest_progress_contract_paths_only",
        })

    if evidence_errors:
        # Treat invalid prior progress as zero progress.  The goal policies above
        # were already compiled from contract topology with an empty progress
        # set, so keeping their frontier fails closed instead of widening back to
        # the complete exact capability surface.
        mode = "EVIDENCE_INVALID_ZERO_PROGRESS"
    elif migration_gap_goal_ids:
        mode = "MIXED_ENFORCEMENT"
    else:
        mode = "ENFORCED"

    payload: dict[str, Any] = {
        "version": PRETOOL_EXECUTION_POLICY_VERSION,
        "authority": "provider_tool_surface_only_not_execution_permit",
        "mode": mode,
        "formal_semantic_contract_id": plan.get("formal_semantic_contract_id"),
        "formal_semantic_digest": plan.get("formal_semantic_digest"),
        "source_shadow_plan_digest": plan.get("plan_digest"),
        "capability_registry_version": capability_registry.version,
        "allowed_capability_tools": sorted(allowed_tools),
        "goal_policies": goal_policies,
        "completed_goal_ids": sorted(completed_goal_ids),
        "migration_gap_goal_ids": sorted(set(migration_gap_goal_ids)),
        "dependency_blocked_goal_ids": sorted(set(dependency_blocked_goal_ids)),
        "runtime_evidence_errors": evidence_errors,
        "creates_permit": False,
        "dispatches_tools": False,
        "mutates_semantics": False,
        "mutates_business_state": False,
        "downstream_authorities": [
            "goal_effect_match_proof",
            "capability_gate",
            "execution_permit",
            "target_resolver",
            "transaction_authority",
            "business_service",
        ],
    }
    payload["policy_digest"] = _digest(payload)
    return payload


def execution_policy_prompt_projection(policy: dict[str, Any] | None) -> dict[str, Any]:
    row = policy if isinstance(policy, dict) else {}
    return {
        "version": row.get("version"),
        "mode": row.get("mode"),
        "allowed_capability_tools": list(row.get("allowed_capability_tools") or []),
        "goals": [
            {
                "goal_id": item.get("goal_id"),
                "status": item.get("status"),
                "allowed_tools": list(item.get("allowed_tools") or []),
                "missing_dependency_goal_ids": list(item.get("missing_dependency_goal_ids") or []),
            }
            for item in list(row.get("goal_policies") or [])
            if isinstance(item, dict)
        ],
        "rule": (
            "只能调用 allowed_capability_tools 中的业务能力；该边界只限制候选能力，"
            "参数、对象、权限、Permit、事务和业务事实仍由后续 Runtime 验证。"
        ),
    }


__all__ = [
    "PRETOOL_EXECUTION_POLICY_VERSION",
    "build_pretool_execution_policy",
    "execution_policy_prompt_projection",
]
