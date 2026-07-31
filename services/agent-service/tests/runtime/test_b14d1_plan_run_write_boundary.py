from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from agent_core.lifecycle.plan_execution import create_plan_run, freeze_plan_definition
from agent_core.lifecycle import workflow_runtime


def _plan() -> dict:
    return {
        "plan_contract_version": "grounded-execution-plan@2",
        "workflow_id": "workflow:b14d1",
        "turn_plan_id": "turn-plan:b14d1",
        "formal_semantic_contract_id": "semantic:b14d1",
        "formal_semantic_digest": "semantic-digest-b14d1",
        "goal_source": "frozen_semantic_contract",
        "level": "DIRECT",
        "status": "PLANNED",
        "goal": "查询订单",
        "goals": [{
            "goal_id": "goal:formal",
            "goal_type": "query",
            "description": "查询正式订单目标",
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
                "formal_effect_completion_eligible": True,
                "expected_result_cardinality": "single",
                "effect_result_cardinality_hint": "single",
            },
        }],
        "reasons": ["single_goal"],
    }


def _success() -> dict:
    return {
        "ok": True,
        "code": "ORDER_FOUND",
        "message": "已查询到订单。",
        "data": {"order": {"order_id": "10001"}},
        "runtime_outcome": {
            "outcome_type": "observation",
            "effects": "none",
            "next_interaction": "none",
        },
    }


def test_plan_run_step_update_is_derived_from_authorities_not_compatibility_projection() -> None:
    assert hasattr(workflow_runtime, "derive_plan_run_step_update"), (
        "B14d1 requires a PlanRun-owned result derivation boundary."
    )
    derive = getattr(workflow_runtime, "derive_plan_run_step_update")
    definition = freeze_plan_definition(_plan())
    plan_run = create_plan_run(definition, turn_index=11)

    # A compatibility view can be stale or locally mutated. It must not be an
    # input to the PlanRun write decision.
    forged_projection = workflow_runtime.project_plan_runtime(
        definition=definition,
        plan_run=plan_run,
    )
    forged_projection = deepcopy(forged_projection)
    forged_projection["steps"][0]["status"] = "FAILED_FINAL"
    forged_projection["steps"][0]["verification"] = {
        "goal_completion_eligible": False,
        "verified_by_runtime": False,
        "last_result_code": "FORGED",
    }

    update = derive(
        definition=definition,
        plan_run=plan_run,
        effect_id="effect:formal",
        result=_success(),
    )

    assert update["status"] == "SUCCEEDED"
    assert update["failure_type"] == "NONE"
    assert update["verification"]["last_result_code"] == "ORDER_FOUND"
    assert update["verification"]["goal_completion_eligible"] is True
    assert update["verification"]["verified_by_runtime"] is True


def test_tool_execution_does_not_use_projection_mutation_as_plan_run_write_input() -> None:
    service_root = Path.cwd()
    if not (service_root / "src" / "agent_core").is_dir():
        service_root = Path(__file__).resolve().parents[2]
    source = (
        service_root
        / "src"
        / "agent_core"
        / "lifecycle"
        / "tool_execution_runtime.py"
    ).read_text(encoding="utf-8")

    assert "mark_step_result(" not in source
    assert "complete_plan_run_step_result(" in source



def test_repaired_candidate_step_is_persisted_in_plan_run_not_only_projection() -> None:
    from agent_core.lifecycle.plan_execution import begin_step_attempt, project_grounded_execution_plan

    plan = _plan()
    plan["steps"] = [
        {
            **deepcopy(plan["steps"][0]),
            "step_id": "step:first",
            "effect_id": "effect:first",
            "verification": {
                **deepcopy(plan["steps"][0]["verification"]),
                "goal_completion_types": ["order_lookup"],
            },
        },
        {
            **deepcopy(plan["steps"][0]),
            "step_id": "step:second",
            "effect_id": "effect:second",
            "verification": {
                **deepcopy(plan["steps"][0]["verification"]),
                "goal_completion_types": ["order_lookup"],
            },
        },
    ]
    plan["tasks"][0]["step_ids"] = ["step:first", "step:second"]
    definition = freeze_plan_definition(plan)
    run = create_plan_run(definition, turn_index=11)

    run, first_attempt = begin_step_attempt(
        definition=definition,
        plan_run=run,
        effect_id="effect:first",
        tool_name="get_order",
        args={},
        execution_permit=None,
    )
    run, _ = workflow_runtime.complete_plan_run_step_result(
        definition=definition,
        plan_run=run,
        attempt_id=first_attempt["attempt_id"],
        effect_id="effect:first",
        result={
            "ok": False,
            "code": "CAPABILITY_EXACT_MATCH_REQUIRED",
            "message": "候选能力不精确。",
        },
    )

    run, second_attempt = begin_step_attempt(
        definition=definition,
        plan_run=run,
        effect_id="effect:second",
        tool_name="get_order",
        args={},
        execution_permit=None,
    )
    run, _ = workflow_runtime.complete_plan_run_step_result(
        definition=definition,
        plan_run=run,
        attempt_id=second_attempt["attempt_id"],
        effect_id="effect:second",
        result=_success(),
    )

    final_projection = project_grounded_execution_plan(definition=definition, plan_run=run)
    repaired = next(row for row in final_projection["steps"] if row["effect_id"] == "effect:first")
    assert repaired["status"] == "SKIPPED"
    assert repaired["verification"]["superseded_by_effect_id"] == "effect:second"
    assert repaired["verification"]["candidate_repaired"] is True
    assert final_projection["status"] == "SUCCEEDED"
