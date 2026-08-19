from __future__ import annotations

"""Read-only projection of an existing transaction interaction into PlanRun.

Transaction Repository/Draft state remains the sole transaction authority. This
module never creates a Draft, Grant, Attempt, Receipt or ExecutionPermit and
never dispatches a business capability. It only projects an already-existing
human-input pause into the current PlanDefinition/PlanRun.
"""

from copy import deepcopy
from typing import Any

from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.kernel.plan_projection_contract import derive_plan_runtime_status
from agent_core.ledger import find_handle, scope_for_state
from agent_core.lifecycle.semantic_contract import semantic_goals
from agent_core.lifecycle.workflow_runtime import (
    build_workflow_plan,
    materialize_plan_runtime,
    project_plan_runtime,
    validate_grounded_execution_plan,
)
from agent_core.runtime.capability_effects import discover_exact_effect_surface
from agent_core.runtime.capability_gate import build_effects
from agent_core.storage.repositories.base import TransactionLifecycleRepository, TransactionScope
from agent_core.transaction.active_draft import get_active_draft_id

_PROJECTION_AUTHORITY = "transaction_repository_read_through_to_plan_run"
_INTERACTION_TO_STEP_STATUS = {
    "awaiting_authority": "AWAITING_AUTHORIZATION",
    "collecting_input": "NEEDS_INPUT",
}


def _transaction_scope(state: dict[str, Any]) -> TransactionScope:
    return TransactionScope(
        tenant_id=str(state.get("current_tenant_id") or "default"),
        user_id=str(state.get("current_user_id") or ""),
        thread_id=str(state.get("current_thread_id") or "") or None,
    )


def _durable_origin_completion(
    *,
    state: dict[str, Any],
    transactions: TransactionLifecycleRepository | None,
    capability_registry: CapabilityRegistry,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Bind one live Draft to its exact runtime-created completion provenance.

    Draft identity/state/command data are accepted only from Transaction
    Repository. ``ready_source_tool`` is orchestration provenance stamped by
    ToolExecutionRuntime when that exact ledger Draft became gateway-ready; it
    can choose a Plan completion implementation only after the durable Draft
    and ledger projection agree and exact capability discovery proves the tool
    completes the current frozen Goal. It grants no transaction authority.
    """
    if transactions is None:
        return None
    goals = [
        dict(row)
        for row in semantic_goals(state)
        if isinstance(row, dict) and bool(row.get("required", True))
    ]
    if len(goals) != 1:
        return None
    goal_id = str(goals[0].get("goal_id") or "")
    draft_id = str(get_active_draft_id(state) or "")
    if not goal_id or not draft_id:
        return None

    durable = transactions.get_draft_for_scope(
        scope=_transaction_scope(state),
        draft_id=draft_id,
    )
    if not isinstance(durable, dict):
        return None
    durable_state = str(durable.get("draft_state") or "").upper()
    if durable_state not in {"AWAITING_AUTHORIZATION", "NEEDS_INPUT"}:
        return None
    durable_projection = (
        durable.get("projection")
        if isinstance(durable.get("projection"), dict)
        else {}
    )
    if str(durable_projection.get("draft_id") or durable_projection.get("handle") or "") != draft_id:
        return None

    ledger = list(state.get("artifact_ledger") or [])
    offer = find_handle(
        ledger,
        draft_id,
        scope=scope_for_state(state),
        allowed_kinds={"offer"},
        active_only=False,
    )
    if not isinstance(offer, dict):
        return None
    identity_fields = ("action_id", "command_digest", "operation_capability_digest")
    for field in identity_fields:
        authoritative = str(durable_projection.get(field) or durable.get(field) or "")
        projected = str(offer.get(field) or "")
        if authoritative and authoritative != projected:
            return None
    if str(offer.get("draft_state") or "").upper() != durable_state:
        return None

    source_tool = str(offer.get("ready_source_tool") or "")
    if not source_tool:
        return None
    surface = discover_exact_effect_surface(capability_registry, goals)
    surface_row = next((
        row
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "") == goal_id
    ), None)
    if not isinstance(surface_row, dict) or str(surface_row.get("status") or "") != "exact_supported":
        return None
    completion_tools = {
        str(value)
        for value in list(surface_row.get("completion_tools") or [])
        if str(value)
    }
    if source_tool not in completion_tools:
        return None
    contract = capability_registry.contract_for_tool(source_tool)
    if contract is None or str(contract.execution_kind or "") != "action_draft":
        return None
    return surface, [{
        "id": f"transaction-origin-projection:{draft_id}",
        "name": source_tool,
        "args": {"goal_ids": [goal_id]},
    }]


def _mark_projection_steps(
    workflow_plan: dict[str, Any], *, interaction_id: str,
) -> dict[str, Any]:
    plan = deepcopy(workflow_plan)
    for raw in list(plan.get("steps") or []):
        if not isinstance(raw, dict):
            continue
        raw["verification"] = {
            **dict(raw.get("verification") or {}),
            "projection_only_existing_interaction": True,
            "dispatch_allowed": False,
            "projection_authority": _PROJECTION_AUTHORITY,
            "transaction_interaction_id": interaction_id,
            "creates_execution_permit": False,
            "creates_transaction_authority": False,
        }
    return plan


def project_pending_transaction_interaction(
    *,
    state: dict[str, Any],
    patch: dict[str, Any],
    capability_registry: CapabilityRegistry,
    transactions: TransactionLifecycleRepository | None = None,
) -> dict[str, Any]:
    """Project one durable structured-transaction pause into PlanRun."""
    if str(patch.get("status") or "") != "PendingInteractionActionRedirect":
        return patch
    current = {**state, **patch}
    response_contract = current.get("response_contract") if isinstance(current.get("response_contract"), dict) else {}
    interaction = response_contract.get("interaction") if isinstance(response_contract.get("interaction"), dict) else {}
    interaction_id = str(interaction.get("interaction_id") or "")
    step_status = _INTERACTION_TO_STEP_STATUS.get(str(interaction.get("lifecycle") or ""))
    if not interaction_id or not step_status or interaction_id != str(get_active_draft_id(current) or ""):
        return patch

    discovered = _durable_origin_completion(
        state=current,
        transactions=transactions,
        capability_registry=capability_registry,
    )
    if discovered is None:
        return patch
    surface, calls = discovered
    prior_turn_plan = current.get("current_turn_plan") if isinstance(current.get("current_turn_plan"), dict) else {}
    plan_id = str(prior_turn_plan.get("plan_id") or f"turn-plan:transaction-interaction:{current.get('turn_index') or 0}")
    effects, decorated_calls = build_effects(
        plan_id=plan_id,
        calls=calls,
        capability_registry=capability_registry,
        existing_effects=[],
    )
    planning_state = {**current, "capability_surface": deepcopy(surface)}
    synthetic_turn_plan = {
        "plan_id": plan_id,
        "turn": int(current.get("turn_index") or 0),
        "tool_calls": decorated_calls,
        "effects": effects,
        "semantic_authority": "none_projection_only",
        "workflow_authority": "orchestration_only_not_business_fact",
    }
    workflow_plan = build_workflow_plan(
        state=planning_state,
        turn_plan=synthetic_turn_plan,
        user_text=str(current.get("current_user_input") or ""),
    )
    workflow_plan = _mark_projection_steps(workflow_plan, interaction_id=interaction_id)
    semantic_contract = current.get("frozen_semantic_contract") if isinstance(current.get("frozen_semantic_contract"), dict) else None
    validation = validate_grounded_execution_plan(plan=workflow_plan, semantic_contract=semantic_contract)
    if str(validation.get("status") or "") != "ACCEPTED":
        return patch
    workflow_plan["validation"] = validation
    workflow_plan["plan_digest"] = str(validation.get("structure_digest") or "")
    workflow_plan["immutable_structure"] = True

    definition, plan_run, _ = materialize_plan_runtime(state=planning_state, workflow_plan=workflow_plan)
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
            "result_summary": "durable transaction interaction projected; no capability dispatch occurred",
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
    run["updated_turn"] = int(current.get("turn_index") or 0)
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
