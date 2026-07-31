from __future__ import annotations

from pathlib import Path

from agent_core.lifecycle.plan_execution import (
    create_plan_run,
    freeze_plan_definition,
    project_grounded_execution_plan,
    record_terminal_goal_outcome,
)


def _terminal_only_plan() -> dict:
    return {
        "plan_contract_version": "grounded-execution-plan@2",
        "workflow_id": "workflow:b14d2a",
        "turn_plan_id": "turn-plan:b14d2a",
        "formal_semantic_contract_id": "semantic:b14d2a",
        "formal_semantic_digest": "semantic-digest:b14d2a",
        "goal_source": "frozen_semantic_contract",
        "level": "DIRECT",
        "status": "PLANNED",
        "goal": "直接回答或澄清当前目标",
        "goals": [{
            "goal_id": "goal:terminal",
            "goal_type": "answer",
            "description": "直接回答或澄清当前目标",
            "evidence_span": "继续",
            "requested_effect": {"operation": "answer", "resource": "conversation"},
            "required": True,
            "depends_on": [],
            "coverage_status": "PENDING",
        }],
        "tasks": [{
            "task_id": "task:terminal",
            "goal_id": "goal:terminal",
            "goal_ids": ["goal:terminal"],
            "step_ids": [],
            "status": "PLANNED",
        }],
        "steps": [],
        "reasons": ["terminal_only"],
    }


def _authorities() -> tuple[dict, dict]:
    definition = freeze_plan_definition(_terminal_only_plan())
    return definition, create_plan_run(definition, turn_index=9)


def _assert_status_matches_projection(definition: dict, plan_run: dict, expected: str) -> None:
    projection = project_grounded_execution_plan(
        definition=definition,
        plan_run=plan_run,
    )
    assert plan_run["status"] == projection["status"] == expected


def test_new_plan_run_status_uses_kernel_projection_derivation() -> None:
    definition, plan_run = _authorities()

    _assert_status_matches_projection(definition, plan_run, "RUNNING")


def test_clarification_terminal_outcome_cannot_write_a_second_workflow_status() -> None:
    definition, plan_run = _authorities()
    updated = record_terminal_goal_outcome(
        definition=definition,
        plan_run=plan_run,
        goal_ids=["goal:terminal"],
        terminal_tool="ask_user_clarification",
    )

    _assert_status_matches_projection(definition, updated, "NOT_REQUIRED")
    assert updated["terminal_goal_states"]["goal:terminal"]["handling_status"] == "BLOCKED"


def test_final_answer_terminal_outcome_cannot_write_a_second_workflow_status() -> None:
    definition, plan_run = _authorities()
    updated = record_terminal_goal_outcome(
        definition=definition,
        plan_run=plan_run,
        goal_ids=["goal:terminal"],
        terminal_tool="final_answer",
    )

    _assert_status_matches_projection(definition, updated, "NOT_REQUIRED")
    assert updated["terminal_goal_states"]["goal:terminal"]["handling_status"] == "COVERED"


def test_plan_execution_has_no_private_workflow_status_deriver() -> None:
    service_root = Path.cwd()
    if not (service_root / "src" / "agent_core").is_dir():
        service_root = Path(__file__).resolve().parents[2]
    source = (
        service_root
        / "src"
        / "agent_core"
        / "lifecycle"
        / "plan_execution.py"
    ).read_text(encoding="utf-8")

    assert "def _run_status(" not in source
    assert 'run["status"] = "NEEDS_INPUT"' not in source
    assert 'run["status"] = "SUCCEEDED"' not in source
    assert "derive_plan_runtime_status(" in source
