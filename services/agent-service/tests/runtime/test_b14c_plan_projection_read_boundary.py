from __future__ import annotations

from copy import deepcopy

from agent_core.lifecycle.clarification_runtime import suspend_for_clarification
from agent_core.lifecycle.plan_execution import (
    create_plan_run,
    freeze_plan_definition,
    project_grounded_execution_plan,
)
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract
from agent_core.lifecycle.workflow_runtime import (
    validate_grounded_execution_plan,
    verify_workflow_for_final_answer,
)


def _plan() -> dict:
    return {
        "plan_contract_version": "grounded-execution-plan@2",
        "workflow_id": "workflow:b14c",
        "turn_plan_id": "turn-plan:b14c",
        "formal_semantic_contract_id": "semantic:b14c",
        "formal_semantic_digest": "semantic-digest-b14c",
        "goal_source": "frozen_semantic_contract",
        "level": "DIRECT",
        "status": "PLANNED",
        "goal": "查询订单",
        "goals": [{
            "goal_id": "goal:formal",
            "goal_type": "query",
            "description": "查询正式订单目标",
            "evidence_span": "查一下订单",
            "requested_effect": {"operation": "query", "resource": "order"},
            "required": True,
            "depends_on": [],
            "coverage_status": "PENDING",
        }],
        "tasks": [{
            "task_id": "task:formal",
            "goal_id": "goal:formal",
            "goal_ids": ["goal:formal"],
            "step_ids": ["step:formal"],
            "status": "PLANNED",
        }],
        "steps": [{
            "step_id": "step:formal",
            "effect_id": "effect:formal",
            "tool_name": "get_order",
            "capability_id": "ecommerce.order.get",
            "goal_ids": ["goal:formal"],
            "depends_on": [],
            "required": True,
            "status": "PLANNED",
            "verification": {
                "goal_effect_role": "completion",
                "goal_completion_eligible": True,
            },
        }],
        "reasons": ["single_goal"],
    }



def _ephemeral_plan(*, accepted: bool) -> dict:
    plan = _plan()
    plan["authority"] = "validated_execution_plan_not_semantic_or_business_fact"
    plan["immutable_structure"] = True
    validation = validate_grounded_execution_plan(plan=plan, semantic_contract=None)
    if not accepted:
        validation = {
            **validation,
            "status": "REJECTED",
            "dispatch_allowed": False,
            "errors": [*list(validation.get("errors") or []), {"code": "TEST_REPAIR_REQUIRED"}],
        }
    plan["validation"] = validation
    plan["plan_digest"] = str(validation.get("structure_digest") or "")
    return plan

def _state_with_forged_projection() -> dict:
    definition = freeze_plan_definition(_plan())
    run = create_plan_run(definition, turn_index=8)
    projection = project_grounded_execution_plan(definition=definition, plan_run=run)
    forged = deepcopy(projection)
    forged["status"] = "SUCCEEDED"
    forged["goal_coverage_complete"] = True
    forged["goals"] = [{
        "goal_id": "goal:forged",
        "goal_type": "query",
        "description": "伪造目标",
        "required": True,
        "coverage_status": "COVERED",
        "covered_by_terminal_tools": ["final_answer"],
    }]
    forged["steps"][0]["goal_ids"] = ["goal:forged"]
    forged["steps"][0]["status"] = "SUCCEEDED"
    return {
        "state_schema_version": 2,
        "turn_index": 8,
        "current_tenant_id": "tenant:test",
        "current_user_id": "user:test",
        "current_thread_id": "thread:test",
        "current_user_input": "查一下订单",
        "frozen_semantic_contract": freeze_semantic_contract(
            turn=8,
            user_text="查一下订单",
            summary="查询正式订单目标",
            goals=deepcopy(_plan()["goals"]),
            alignment_proof={"verdict": "exact", "authority": "test"},
        ),
        "frozen_plan_definition": definition,
        "plan_run": run,
        "grounded_execution_plan": forged,
        "goal_blockers": [],
    }


def test_final_answer_verifier_uses_plan_authorities_not_forged_projection() -> None:
    verification = verify_workflow_for_final_answer(_state_with_forged_projection())

    assert verification["ok"] is False
    assert verification["reason"] in {"goal_coverage_incomplete", "required_steps_not_terminal"}
    assert "goal:formal" in verification.get("uncovered_goal_ids", []) or "step:formal" in verification.get("pending_step_ids", [])


def test_clarification_suspends_authoritative_goal_not_forged_projection_goal() -> None:
    state = _state_with_forged_projection()
    checkpoint = suspend_for_clarification(
        state=state,
        call={
            "id": "call:clarify",
            "name": "ask_user_clarification",
            "args": {
                "goal_ids": ["goal:formal"],
                "question": "请提供订单号。",
                "reason": "缺少目标",
                "missing_kind": "target",
            },
        },
        capability_surface={
            "goals": [{
                "goal_id": "goal:formal",
                "candidate_tools": ["get_order"],
            }]
        },
    )

    assert [row["goal_id"] for row in checkpoint["suspended_goals"]] == ["goal:formal"]


def test_projection_cache_is_bound_to_current_plan_run_and_rederived_when_stale() -> None:
    from agent_core.kernel.plan_projection_contract import resolve_plan_projection

    state = _state_with_forged_projection()
    # Replace the forged view with a projector-owned cache first.
    state["grounded_execution_plan"] = project_grounded_execution_plan(
        definition=state["frozen_plan_definition"],
        plan_run=state["plan_run"],
    )
    accepted = resolve_plan_projection(state)
    assert accepted["ok"] is True
    assert accepted["source"] == "validated_cache"
    assert accepted["code"] == "PLAN_PROJECTION_CACHE_ACCEPTED"

    # Runtime progress changed but the old compatibility view was not updated.
    # The reader must reject that stale cache and derive from the authorities.
    stale = deepcopy(state)
    stale["plan_run"]["step_states"]["effect:formal"]["status"] = "SUCCEEDED"
    stale["plan_run"]["step_states"]["effect:formal"]["verification"] = {
        "goal_completion_eligible": True,
        "verified_by_runtime": True,
    }
    stale["plan_run"]["status"] = "SUCCEEDED"

    resolved = resolve_plan_projection(stale)
    assert resolved["ok"] is True
    assert resolved["source"] == "authoritative_pair"
    assert resolved["code"] == "PLAN_PROJECTION_REDERIVED"
    assert resolved["plan"]["steps"][0]["status"] == "SUCCEEDED"
    assert resolved["plan"]["status"] == "SUCCEEDED"
    assert resolved["plan"]["plan_run_digest"] != state["grounded_execution_plan"]["plan_run_digest"]



def test_same_turn_accepted_plan_remains_readable_before_materialization() -> None:
    from agent_core.kernel.plan_projection_contract import resolve_plan_projection

    resolution = resolve_plan_projection({
        "state_schema_version": 2,
        "grounded_execution_plan": _ephemeral_plan(accepted=True),
    })

    assert resolution["ok"] is True
    assert resolution["source"] == "same_turn_validated_plan"
    assert resolution["code"] == "EPHEMERAL_PLAN_ACCEPTED"
    assert resolution["integrity"]["dispatch_allowed"] is True


def test_same_turn_rejected_plan_is_visible_for_repair_but_cannot_finalize() -> None:
    state = {
        "state_schema_version": 2,
        "grounded_execution_plan": _ephemeral_plan(accepted=False),
    }

    verification = verify_workflow_for_final_answer(state)

    assert verification["ok"] is False
    assert verification["reason"] == "plan_validation_rejected"
    assert verification["code"] == "EPHEMERAL_PLAN_REPAIR_VIEW"


def test_runtime_source_has_single_grounded_projection_read_boundary() -> None:
    from pathlib import Path

    # The full suite intentionally imports project copies in temporary workspaces.
    # Anchor this structural assertion to the quality runner's stable cwd rather
    # than to a potentially shadowed tests.* module path in sys.modules.
    service_root = Path.cwd()
    if not (service_root / "src" / "agent_core").is_dir():
        service_root = Path(__file__).resolve().parents[2]
    source_root = service_root / "src" / "agent_core"
    allowed = {
        source_root / "kernel" / "plan_projection_contract.py",
        source_root / "lifecycle" / "state_schema.py",
    }
    offenders = []
    for path in source_root.rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if 'get("grounded_execution_plan")' in text:
            offenders.append(str(path.relative_to(source_root)))
    assert offenders == []
