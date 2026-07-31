from __future__ import annotations

from agent_core.context.visible_result_refs import mark_visible_result_refs
from agent_core.ledger import artifact_entry, result_entry
from agent_core.lifecycle.workflow_runtime import build_workflow_plan


def _visible_evidence_state(*, goal_type: str = "query") -> tuple[dict, dict]:
    scope = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-a"}
    artifact = artifact_entry(
        resource_type="order",
        resource_id="10002",
        label="机械键盘（订单 10002）",
        facts={"amount": 399},
        scope=scope,
        turn=1,
        source="test",
        handle="artifact:order:10002",
    )
    result = result_entry(
        capability="list_orders",
        member_handles=[artifact["handle"]],
        labels=[artifact["label"]],
        scope=scope,
        turn=1,
        source_target={"mode": "all_orders"},
        handle="result:orders:visible",
    )
    state = {
        "current_tenant_id": "tenant-a",
        "current_user_id": "u001",
        "current_thread_id": "thread-a",
        "turn_index": 2,
        "artifact_ledger": [artifact, result],
        "turn_goal_plan": {
            "goals": [{
                "goal_id": "g1",
                "description": "回答最贵的订单",
                "evidence_span": "最贵的是哪个",
                "goal_type": goal_type,
                "required": True,
                "depends_on": [],
            }]
        },
    }
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state=state, evidence_handles=[result["handle"]]
    )
    turn_plan = {
        "plan_id": "turn-plan:evidence-reuse",
        "effects": [],
        "tool_calls": [{
            "name": "respond_to_user",
            "args": {
                "answer": "最贵的是机械键盘。",
                "evidence_handles": [result["handle"]],
            },
            "_goal_ids": ["g1"],
        }],
    }
    return state, turn_plan


def test_visible_historical_evidence_satisfies_bound_query_goal() -> None:
    state, turn_plan = _visible_evidence_state()
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text="最贵的是哪个")
    goal = workflow["goals"][0]
    assert goal["coverage_status"] == "COVERED"
    assert goal["satisfaction_proof"]["kind"] == "historical_visible_evidence"
    assert workflow["goal_coverage_complete"] is True


def test_visible_collection_member_can_join_parent_as_historical_evidence() -> None:
    state, turn_plan = _visible_evidence_state()
    result_handle = turn_plan["tool_calls"][0]["args"]["evidence_handles"][0]
    turn_plan["tool_calls"][0]["args"]["evidence_handles"] = [
        result_handle,
        "artifact:order:10002",
    ]

    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text="最贵的是哪个")

    goal = workflow["goals"][0]
    assert goal["coverage_status"] == "COVERED"
    assert goal["satisfaction_proof"]["evidence_handles"] == [
        result_handle,
        "artifact:order:10002",
    ]
    assert goal["satisfaction_proof"]["member_provenance"][1] == {
        "evidence_handle": "artifact:order:10002",
        "source_collection_ref": result_handle,
        "presentation_origin": "customer_visible_result_member",
    }


def test_historical_evidence_never_satisfies_action_goal() -> None:
    state, turn_plan = _visible_evidence_state(goal_type="action")
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text="把最贵的退了")
    assert workflow["goals"][0]["coverage_status"] == "PENDING"
    assert workflow["goal_coverage_complete"] is False
