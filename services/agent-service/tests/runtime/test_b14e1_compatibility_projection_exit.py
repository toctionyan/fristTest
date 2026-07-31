from __future__ import annotations

from contextlib import closing

import json
import sqlite3
from pathlib import Path

from agent_core.lifecycle.plan_execution import (
    create_plan_run,
    freeze_plan_definition,
    project_grounded_execution_plan,
)


def _plan() -> dict:
    return {
        "plan_contract_version": "grounded-execution-plan@2",
        "workflow_id": "workflow:b14e1",
        "turn_plan_id": "turn-plan:b14e1",
        "formal_semantic_contract_id": "semantic:b14e1",
        "formal_semantic_digest": "semantic-digest-b14e1",
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
            },
        }],
        "reasons": ["single_goal"],
    }


def test_production_runtime_has_no_projection_mutation_compatibility_api() -> None:
    service_root = Path(__file__).resolve().parents[2]
    source = (
        service_root
        / "src"
        / "agent_core"
        / "lifecycle"
        / "workflow_runtime.py"
    ).read_text(encoding="utf-8")

    assert "def mark_step_result(" not in source
    assert "mark_step_result" not in source


def test_preprod_diagnostics_reads_canonical_projection_not_retired_workflow_plan(
    tmp_path: Path,
) -> None:
    from scripts.verify_preprod_full_lifecycle import _graph_diagnostics

    definition = freeze_plan_definition(_plan())
    plan_run = create_plan_run(definition, turn_index=14)
    state = {
        "state_schema_version": 2,
        "phase": "verify",
        "status": "running",
        "frozen_plan_definition": definition,
        "plan_run": plan_run,
        "grounded_execution_plan": None,
        "workflow_plan": {
            "status": "FAILED_FINAL",
            "goal_coverage_complete": True,
            "goals": [{"goal_id": "goal:forged", "coverage_status": "COVERED"}],
        },
        "tool_trace": [],
        "decision_chain": [],
    }
    database = tmp_path / "trace.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE trace_logs (id INTEGER PRIMARY KEY, event_type TEXT, output_json TEXT)"
        )
        connection.execute(
            "INSERT INTO trace_logs(event_type, output_json) VALUES (?, ?)",
            ("graph_snapshot", json.dumps({"state": state}, ensure_ascii=False)),
        )
        connection.commit()

    diagnostics = _graph_diagnostics(database)
    assert diagnostics
    workflow = diagnostics[0]["workflow"]
    canonical = project_grounded_execution_plan(
        definition=definition,
        plan_run=plan_run,
    )
    assert workflow["status"] == canonical["status"]
    assert workflow["goal_coverage_complete"] == canonical["goal_coverage_complete"]
    assert workflow["goals"] == [
        {"id": goal.get("goal_id"), "status": goal.get("coverage_status")}
        for goal in canonical["goals"]
    ]
    assert workflow["goals"] != [{"id": "goal:forged", "status": "COVERED"}]


def test_clarification_blocker_projection_receives_current_plan_authorities() -> None:
    service_root = Path(__file__).resolve().parents[2]
    source = (
        service_root
        / "src"
        / "agent_core"
        / "lifecycle"
        / "dialogue_runtime.py"
    ).read_text(encoding="utf-8")
    start = source.index("clarification_state = {")
    end = source.index("clarification_patch[\"goal_blockers\"]", start)
    block = source[start:end]

    assert "**state" in block
    assert "**common" in block
    assert '"grounded_execution_plan": workflow_plan' in block


def test_goal_blocker_runtime_has_no_unused_singleton_clarification_projection() -> None:
    service_root = Path(__file__).resolve().parents[2]
    source = (
        service_root
        / "src"
        / "agent_core"
        / "lifecycle"
        / "goal_blockers.py"
    ).read_text(encoding="utf-8")

    assert "legacy_pending_clarification_projection" not in source
    assert "pending-clarification.compatibility@2" not in source
