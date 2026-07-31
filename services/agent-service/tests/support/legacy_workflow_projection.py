from __future__ import annotations

"""Test-only compatibility projection mutation helper.

Production code must write PlanRun and reproject through Kernel.  Historical
unit tests that exercise pre-PlanRun ephemeral plans use this helper so the
retired mutation API cannot be imported by serving code.
"""

from copy import deepcopy
from typing import Any

from agent_core.kernel.plan_projection_contract import derive_plan_runtime_view
from agent_core.lifecycle.candidate_repair import is_candidate_repairable_result
from agent_core.lifecycle.workflow_contracts import StepStatus
from agent_core.lifecycle.workflow_runtime import _derive_step_result_update_from_step


def mark_step_result(
    *,
    workflow_plan: dict[str, Any] | None,
    effect_id: str,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(workflow_plan, dict) or not effect_id:
        return workflow_plan
    plan = deepcopy(workflow_plan)
    updated = False
    updated_status: str | None = None
    updated_goal_ids: set[str] = set()
    updated_completion_types: set[str] = set()
    next_steps: list[dict[str, Any]] = []
    for step in list(plan.get("steps") or []):
        if not isinstance(step, dict):
            continue
        row = dict(step)
        if str(row.get("effect_id") or "") == effect_id:
            update = _derive_step_result_update_from_step(step=row, result=result)
            status = str(update.get("status") or StepStatus.FAILED_FINAL.value)
            updated_status = status
            updated_goal_ids = {
                str(value) for value in list(row.get("goal_ids") or []) if str(value)
            }
            updated_completion_types = {
                str(value)
                for value in list(
                    (row.get("verification") or {}).get("goal_completion_types") or []
                )
                if str(value)
            }
            row.update(update)
            updated = True
        next_steps.append(row)
    if not updated:
        return plan
    if updated_status == StepStatus.SUCCEEDED.value and updated_goal_ids:
        repaired_steps: list[dict[str, Any]] = []
        for step in next_steps:
            row = dict(step)
            prior_goals = {
                str(value) for value in list(row.get("goal_ids") or []) if str(value)
            }
            verification = dict(row.get("verification") or {})
            prior_completion_types = {
                str(value)
                for value in list(verification.get("goal_completion_types") or [])
                if str(value)
            }
            repairable_prior = is_candidate_repairable_result({
                "ok": False,
                "code": verification.get("last_result_code"),
            })
            if (
                str(row.get("effect_id") or "") != effect_id
                and str(row.get("status") or "")
                == StepStatus.FAILED_RETRYABLE.value
                and prior_goals == updated_goal_ids
                and prior_completion_types == updated_completion_types
                and repairable_prior
            ):
                row["status"] = StepStatus.SKIPPED.value
                row["required"] = False
                row["failure_reason"] = None
                verification["superseded_by_effect_id"] = effect_id
                verification["candidate_repaired"] = True
                row["verification"] = verification
            repaired_steps.append(row)
        next_steps = repaired_steps
    runtime_view = derive_plan_runtime_view(
        goals=list(plan.get("goals") or []),
        tasks=list(plan.get("tasks") or []),
        steps=next_steps,
    )
    plan["steps"] = runtime_view["steps"]
    plan["updated_turn"] = int(plan.get("updated_turn") or 0)
    plan["goals"] = runtime_view["goals"]
    plan["goal_coverage_complete"] = runtime_view["goal_coverage_complete"]
    plan["status"] = runtime_view["status"]
    plan["tasks"] = runtime_view["tasks"]
    return plan
