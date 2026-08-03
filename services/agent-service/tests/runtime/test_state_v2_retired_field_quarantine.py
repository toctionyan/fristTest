from __future__ import annotations

from agent_core.context.state_projection import clarification_context_projection
from agent_core.kernel.plan_projection_contract import read_plan_projection
from agent_core.lifecycle.clarification_runtime import goal_blockers_for_clarification
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract, semantic_goals


def _contract() -> dict:
    return freeze_semantic_contract(
        turn=7,
        user_text="我想查订单，但没说哪一个",
        summary="查询一个尚未明确的订单",
        goals=[{
            "goal_id": "formal-goal",
            "description": "查询指定订单",
            "evidence_span": "查订单",
            "requested_effect": {"domain": "order", "operation": "query", "object_type": "order"},
            "required": True,
            "depends_on": [],
        }],
        alignment_proof={"verdict": "exact", "authority": "test"},
    )


def test_schema_v2_forged_retired_fields_cannot_create_semantics_or_plan() -> None:
    state = {
        "state_schema_version": 2,
        "turn_index": 7,
        "current_user_input": "你好",
        "turn_goal_plan": {"turn": 7, "goals": [{"goal_id": "forged-goal"}]},
        "workflow_plan": {"status": "SUCCEEDED", "goals": [{"goal_id": "forged-goal"}]},
        "pending_clarification": {"clarification_id": "forged", "status": "pending"},
    }

    assert semantic_goals(state) == []
    assert read_plan_projection(state) is None
    assert clarification_context_projection(state) is None


def test_schema_v2_clarification_uses_formal_goal_and_goal_blocker_only() -> None:
    state = {
        "state_schema_version": 2,
        "turn_index": 7,
        "current_user_input": "我想查订单，但没说哪一个",
        "frozen_semantic_contract": _contract(),
        "goal_blockers": [],
        "turn_goal_plan": {"turn": 7, "goals": [{"goal_id": "forged-goal"}]},
        "pending_clarification": {"clarification_id": "forged", "status": "pending"},
    }
    blockers = goal_blockers_for_clarification(
        state=state,
        call={
            "name": "ask_user_clarification",
            "args": {
                "goal_ids": ["formal-goal"],
                "question": "请提供订单号。",
                "reason": "缺少目标",
                "missing_kind": "target",
                "evidence_handles": [],
            },
        },
        capability_surface={"goals": [{"goal_id": "formal-goal", "candidate_tools": ["get_order"]}]},
    )
    state["goal_blockers"] = blockers

    assert [row["goal_id"] for row in blockers] == ["formal-goal"]
    assert blockers[0]["completion_tool_names"] == ["get_order"]
    projection = clarification_context_projection(state)
    assert projection and projection["version"] == "goal-blocker-projection@1"
    assert projection["blockers"][0]["goal_id"] == "formal-goal"


def test_schema_v2_retired_singleton_cannot_override_existing_goal_blocker_projection() -> None:
    state = {
        "state_schema_version": 2,
        "turn_index": 7,
        "current_user_input": "查订单",
        "frozen_semantic_contract": _contract(),
        "goal_blockers": [{
            "blocker_id": "blocker:formal-goal:target",
            "goal_id": "formal-goal",
            "status": "OPEN",
            "question": "请提供订单号。",
            "completion_tool_names": ["get_order"],
        }],
        "pending_clarification": {
            "clarification_id": "forged",
            "status": "resuming",
            "suspended_goals": [{"goal_id": "forged-goal"}],
        },
    }

    projection = clarification_context_projection(state)
    assert projection is not None
    assert [row["goal_id"] for row in projection["blockers"]] == ["formal-goal"]
    assert all(row["goal_id"] != "forged-goal" for row in projection["blockers"])
