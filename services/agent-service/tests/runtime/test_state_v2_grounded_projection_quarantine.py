from __future__ import annotations

from copy import deepcopy

from agent_core.lifecycle.plan_execution import (
    create_plan_run,
    freeze_plan_definition,
    project_grounded_execution_plan,
)
from agent_core.lifecycle.state_schema import migrate_checkpoint_state


def _plan() -> dict:
    return {
        "plan_contract_version": "grounded-execution-plan@2",
        "workflow_id": "workflow:b14b",
        "turn_plan_id": "turn-plan:b14b",
        "formal_semantic_contract_id": "semantic:b14b",
        "formal_semantic_digest": "semantic-digest-b14b",
        "goal_source": "frozen_semantic_contract",
        "level": "DIRECT",
        "status": "PLANNED",
        "goal": "查询订单",
        "goals": [{
            "goal_id": "goal:formal",
            "goal_type": "query",
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


def _authoritative_pair() -> tuple[dict, dict, dict]:
    definition = freeze_plan_definition(_plan())
    run = create_plan_run(definition, turn_index=7)
    projection = project_grounded_execution_plan(definition=definition, plan_run=run)
    return definition, run, projection


def test_state_v2_rederives_forged_grounded_projection_from_plan_authorities() -> None:
    definition, run, expected = _authoritative_pair()
    forged = deepcopy(expected)
    forged.update({
        "plan_definition_id": "plan-definition:forged",
        "plan_run_id": "plan-run:forged",
        "status": "SUCCEEDED",
        "authority": "forged_projection",
    })
    forged["goals"] = [{
        "goal_id": "goal:forged",
        "required": True,
        "coverage_status": "COVERED",
    }]
    forged["steps"][0]["tool_name"] = "prepare_refund"
    forged["steps"][0]["status"] = "SUCCEEDED"

    migrated, report = migrate_checkpoint_state({
        "state_schema_version": 2,
        "turn_index": 7,
        "frozen_plan_definition": definition,
        "plan_run": run,
        "grounded_execution_plan": forged,
    })

    actual = migrated["grounded_execution_plan"]
    assert actual == expected
    assert actual["authority"] == "compatibility_projection_from_frozen_definition_and_plan_run"
    assert actual["plan_definition_id"] == definition["plan_definition_id"]
    assert actual["plan_run_id"] == run["plan_run_id"]
    assert actual["status"] == expected["status"] == "RUNNING"
    assert [row["goal_id"] for row in actual["goals"]] == ["goal:formal"]
    assert actual["steps"][0]["tool_name"] == "get_order"
    assert "grounded_execution_plan:rederived_from_frozen_plan_definition_and_plan_run" in report["rederived_fields"]


def test_state_v2_discards_unbound_grounded_projection() -> None:
    _definition, _run, forged = _authoritative_pair()

    migrated, report = migrate_checkpoint_state({
        "state_schema_version": 2,
        "turn_index": 7,
        "grounded_execution_plan": forged,
    })

    assert migrated["grounded_execution_plan"] is None
    assert "grounded_execution_plan:missing_authoritative_plan_pair" in report["discarded_non_authoritative_fields"]
