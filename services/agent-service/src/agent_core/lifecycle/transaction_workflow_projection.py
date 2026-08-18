from __future__ import annotations

"""Read-only projection of an existing transaction interaction into PlanRun.

Transaction Repository/Draft state remains the sole transaction authority. This
module never creates a Draft, Grant, Attempt, Receipt or ExecutionPermit and
never dispatches a business capability. It only gives the current immutable
PlanDefinition a completion-shaped, non-dispatchable step and records the
already-existing human-input pause in PlanRun so Workflow does not claim the
Goal is still freely runnable.
"""

from copy import deepcopy
from typing import Any

from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.kernel.plan_projection_contract import derive_plan_runtime_status
from agent_core.lifecycle.semantic_contract import semantic_goals
from agent_core.lifecycle.workflow_runtime import (
    build_workflow_plan,
    materialize_plan_runtime,
    project_plan_runtime,
    validate_grounded_execution_plan,
)
from agent_core.runtime.capability_effects import discover_exact_effect_surface
from agent_core.runtime.capability_gate import build_effects

_PROJECTION_AUTHORITY = "transaction_interaction_read_projection_to_plan_run"
_INTERACTION_TO_STEP_STATUS = {
    "awaiting_authority": "AWAITING_AUTHORIZATION",
    "collecting_input": "NEEDS_INPUT",
}


def _required_action_completion_calls(
    *,
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    goals = [
        dict(row)
        for row in semantic_goals(state)
        if isinstance(row, dict) and bool(row.get("required", True))
    ]
    if not goals:
        return None
    surface = discover_exact_effect_surface(capability_registry, goals)
    by_goal = {
        str(row.get("goal_id") or ""): row
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }
    calls: list[dict[str, Any]] = []
    for index, goal in enumerate(goals, start=1):
        goal_id = str(goal.get("goal_id") or "")
        row = by_goal.get(goal_id) or {}
        completion_tools = list(dict.fromkeys(
            str(value)
            for value in list(row.get("completion_tools") or [])
            if str(value)
        ))
        if str(row.get("status") or "") != "exact_supported" or len(completion_tools) != 1:
            return None
        tool_name = completion_tools[0]
        contract = capability_registry.contract_for_tool(tool_name)
        if contract is None or str(contract.execution_kind or "") != "action_draft":
            return None
        calls.append({
            "id": f"transaction-interaction-projection:{index}",
            "name": tool_name,
            "args": {"goal_ids": [goal_id]},
        })
    return surface, calls


def _mark_projection_steps(
    workflow_plan: dict[str, Any],
    *,
    interaction_id: str,
) -> dict[str, Any]:
    plan = deepcopy(workflow_plan)
    for raw in list(plan.get("steps") or []):
        if not isinstance(raw, dict):
            continue
        verification = dict(raw.get("verification") or {})
        verification.update({
            "projection_only_existing_interaction": True,
            "dispatch_allowed": False,
            "projection_authority": _PROJECTION_AUTHORITY,
            "transaction_interaction_id": interaction_id,
            "creates_execution_permit": False,
            "creates_transaction_authority": False,
        })
        raw["verification"] = verification
    return plan


def project_pending_transaction_interaction(
    *,
    state: dict[str, Any],
    patch: dict[str, Any],
    capability_registry: CapabilityRegistry,
) -> dict[str, Any]:
    """Project one already-live structured transaction pause into PlanRun.

    The dialogue node has already chosen *not* to invoke the model because a
    durable transaction interaction serializes the pure-write lane. The only
    legal operation here is a read-through orchestration projection: identify
    the exact registered completion capability for each frozen Goal, materialize
    it as a non-dispatchable PlanDefinition step, and set the corresponding
    PlanRun step to the same waiting state reported by the interaction.
    """
    if str(patch.get("status") or "") != "PendingInteractionActionRedirect":
        return patch
    response_contract = (
        patch.get("response_contract")
        if isinstance(patch.get("response_contract"), dict)
        else {}
    )
    interaction = (
        response_contract.get("interaction")
        if isinstance(response_contract.get("interaction"), dict)
        else {}
    )
    interaction_id = str(interaction.get("interaction_id") or "")
    step_status = _INTERACTION_TO_STEP_STATUS.get(str(interaction.get("lifecycle") or ""))
    if not interaction_id or not step_status:
        return patch

    discovered = _required_action_completion_calls(
        state=state,
        capability_registry=capability_registry,
    )
    if discovered is None:
        return patch
    surface, calls = discovered
    prior_turn_plan = state.get("current_turn_plan") if isinstance(state.get("current_turn_plan"), dict) else {}
    plan_id = str(prior_turn_plan.get("plan_id") or f"turn-plan:transaction-interaction:{state.get('turn_index') or 0}")
    effects, decorated_calls = build_effects(
        plan_id=plan_id,
        calls=calls,
        capability_registry=capability_registry,
        existing_effects=[],
    )
    planning_state = {
        **state,
        "capability_surface": deepcopy(surface),
    }
    synthetic_turn_plan = {
        "plan_id": plan_id,
        "turn": int(state.get("turn_index") or 0),
        "tool_calls": decorated_calls,
        "effects": effects,
        "semantic_authority": "none_projection_only",
        "workflow_authority": "orchestration_only_not_business_fact",
    }
    workflow_plan = build_workflow_plan(
        state=planning_state,
        turn_plan=synthetic_turn_plan,
        user_text=str(state.get("current_user_input") or ""),
    )
    workflow_plan = _mark_projection_steps(
        workflow_plan,
        interaction_id=interaction_id,
    )
    semantic_contract = (
        state.get("frozen_semantic_contract")
        if isinstance(state.get("frozen_semantic_contract"), dict)
        else None
    )
    validation = validate_grounded_execution_plan(
        plan=workflow_plan,
        semantic_contract=semantic_contract,
    )
    if str(validation.get("status") or "") != "ACCEPTED":
        return patch
    workflow_plan["validation"] = validation
    workflow_plan["plan_digest"] = str(validation.get("structure_digest") or "")
    workflow_plan["immutable_structure"] = True

    definition, plan_run, _ = materialize_plan_runtime(
        state=planning_state,
        workflow_plan=workflow_plan,
    )
    run = deepcopy(plan_run)
    projected_effect_ids: list[str] = []
    for step in list(definition.get("steps") or []):
        if not isinstance(step, dict):
            continue
        verification = step.get("verification") if isinstance(step.get("verification"), dict) else {}
        if not bool(verification.get("projection_only_existing_interaction")):
            continue
        effect_id = str(step.get("effect_id") or "")
        goal_ids = [str(value) for value in list(step.get("goal_ids") or []) if str(value)]
        if not effect_id or not goal_ids:
            return patch
        step_state = dict((run.get("step_states") or {}).get(effect_id) or {})
        step_state.update({
            "status": step_status,
            "result_summary": "existing transaction interaction projected; no capability dispatch occurred",
            "failure_type": "REQUIRES_HUMAN_INPUT",
            "failure_reason": None,
            "completion_proof": False,
            "verification": {
                "goal_completion_eligible": True,
                "goal_cardinality_eligible": True,
                "goal_completion_eligible_by_goal": {goal_id: True for goal_id in goal_ids},
                "goal_cardinality_eligible_by_goal": {goal_id: True for goal_id in goal_ids},
                "projection_only_existing_interaction": True,
                "dispatch_allowed": False,
                "projection_authority": _PROJECTION_AUTHORITY,
                "transaction_interaction_id": interaction_id,
                "creates_execution_permit": False,
                "creates_transaction_authority": False,
            },
        })
        run["step_states"][effect_id] = step_state
        projected_effect_ids.append(effect_id)

    if not projected_effect_ids:
        return patch
    # A projection is evidence of a wait, never an execution attempt or an
    # outcome. The projected effects therefore keep attempt_count == 0 and add
    # neither PlanRun attempts nor outcomes.
    run["updated_turn"] = int(state.get("turn_index") or 0)
    run["status"] = derive_plan_runtime_status(definition=definition, plan_run=run)
    projection = project_plan_runtime(definition=definition, plan_run=run)
    if str(projection.get("status") or "") != step_status:
        return patch
    return {
        **patch,
        "capability_surface": deepcopy(surface),
        "frozen_plan_definition": definition,
        "plan_run": run,
        "grounded_execution_plan": projection,
    }


__all__ = ["project_pending_transaction_interaction"]
