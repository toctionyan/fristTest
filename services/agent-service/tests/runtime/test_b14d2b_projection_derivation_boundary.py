from __future__ import annotations

from pathlib import Path

from tests.support.legacy_workflow_projection import mark_step_result


def _same_goal_paused_plan() -> dict:
    return {
        "plan_contract_version": "grounded-execution-plan@2",
        "authority": "validated_execution_plan_not_semantic_or_business_fact",
        "immutable_structure": True,
        "workflow_id": "workflow:b14d2b",
        "turn_plan_id": "turn-plan:b14d2b",
        "goal_source": "frozen_semantic_contract",
        "level": "SEQUENTIAL",
        "status": "PLANNED",
        "goals": [{
            "goal_id": "goal:refund",
            "goal_type": "action",
            "description": "申请退款",
            "required": True,
            "depends_on": [],
            "coverage_status": "PENDING",
            "covered_by_terminal_tools": [],
        }],
        "tasks": [{
            "task_id": "task:refund",
            "goal_id": "goal:refund",
            "goal_ids": ["goal:refund"],
            "step_ids": ["step:assess", "step:draft"],
            "status": "PLANNED",
        }],
        "steps": [
            {
                "step_id": "step:assess",
                "effect_id": "effect:assess",
                "tool_name": "evaluate_refund_eligibility",
                "goal_ids": ["goal:refund"],
                "depends_on": [],
                "required": True,
                "status": "PLANNED",
                "verification": {"goal_effect_role": "support"},
            },
            {
                "step_id": "step:draft",
                "effect_id": "effect:draft",
                "tool_name": "prepare_refund_from_eligibility",
                "goal_ids": ["goal:refund"],
                "depends_on": ["effect:assess"],
                "required": True,
                "status": "PLANNED",
                "verification": {
                    "goal_effect_role": "completion",
                    "goal_completion_eligible": True,
                    "completion_owner": "transaction_runtime",
                },
            },
        ],
        "goal_coverage_complete": False,
        "updated_turn": 12,
    }


def _draft_ready() -> dict:
    return {
        "ok": True,
        "code": "DRAFT_READY",
        "message": "退款草稿已准备，等待用户授权。",
        "data": {"draft_id": "draft:b14d2b"},
        "runtime_outcome": {
            "outcome_type": "draft_created",
            "effects": "draft_created",
            "safe_to_continue": True,
            "customer_safe_summary": "退款草稿已准备。",
            "next_interaction": "open_authority",
        },
    }


def test_ephemeral_workflow_uses_kernel_runtime_derivation_for_same_goal_pause() -> None:
    from agent_core.kernel.plan_projection_contract import derive_plan_runtime_view

    updated = mark_step_result(
        workflow_plan=_same_goal_paused_plan(),
        effect_id="effect:draft",
        result=_draft_ready(),
    )
    assert isinstance(updated, dict)
    derived = derive_plan_runtime_view(
        goals=updated["goals"],
        tasks=updated["tasks"],
        steps=updated["steps"],
    )

    assert updated["status"] == derived["status"] == "AWAITING_AUTHORIZATION"
    assert updated["goal_coverage_complete"] == derived["goal_coverage_complete"]
    assert updated["goals"] == derived["goals"]
    assert updated["tasks"] == derived["tasks"]


def test_workflow_runtime_has_no_private_projection_derivers() -> None:
    service_root = Path.cwd()
    if not (service_root / "src" / "agent_core").is_dir():
        service_root = Path(__file__).resolve().parents[2]
    source = (
        service_root
        / "src"
        / "agent_core"
        / "lifecycle"
        / "workflow_runtime.py"
    ).read_text(encoding="utf-8")

    assert "def _refresh_goal_coverage(" not in source
    assert "def _aggregate_workflow_status(" not in source
    assert "def _sync_task_statuses(" not in source
    assert "derive_plan_runtime_view(" in source
