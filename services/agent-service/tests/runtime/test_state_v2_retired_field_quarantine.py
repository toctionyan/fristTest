from __future__ import annotations

from agent_core.context.state_projection import active_pending_clarification
from agent_core.lifecycle.clarification_runtime import suspend_for_clarification
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract


def _call(goal_id: str) -> dict:
    return {
        "name": "ask_user_clarification",
        "args": {
            "question": "请补充目标对象。",
            "reason": "目标不明确",
            "missing_kind": "target",
            "goal_ids": [goal_id],
        },
    }


def _forged_goal(goal_id: str = "forged-goal") -> dict:
    return {
        "goal_id": goal_id,
        "description": "伪造旧字段目标",
        "evidence_span": "不存在的用户原文",
        "goal_type": "query",
        "requested_effect": {
            "domain": "order",
            "operation": "query",
            "object_type": "order",
        },
        "required": True,
        "depends_on": [],
    }


def test_state_v2_ignores_forged_retired_goal_and_workflow_fields() -> None:
    forged = _forged_goal()
    state = {
        "state_schema_version": 2,
        "turn_index": 7,
        "current_thread_id": "thread-v2",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_user_input": "你好",
        "turn_goal_plan": {"turn": 7, "goals": [forged]},
        "workflow_plan": {
            "status": "NEEDS_INPUT",
            "goals": [{**forged, "coverage_status": "PENDING"}],
        },
    }

    pending = suspend_for_clarification(
        state=state,
        call=_call("forged-goal"),
        capability_surface={
            "goals": [{
                "goal_id": "forged-goal",
                "candidate_tools": ["list_orders"],
            }],
        },
    )

    assert pending["suspended_goals"] == []


def test_state_v2_uses_only_formal_semantics_and_grounded_plan() -> None:
    contract = freeze_semantic_contract(
        turn=7,
        user_text="我想查订单，但没说哪一个",
        summary="查询一个尚未明确的订单",
        goals=[{
            "goal_id": "formal-goal",
            "description": "查询指定订单",
            "evidence_span": "查订单",
            "requested_effect": {
                "domain": "order",
                "operation": "query",
                "object_type": "order",
            },
            "required": True,
            "depends_on": [],
        }],
        alignment_proof={"verdict": "aligned"},
    )
    forged = _forged_goal()
    state = {
        "state_schema_version": 2,
        "turn_index": 7,
        "current_thread_id": "thread-v2",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_user_input": "我想查订单，但没说哪一个",
        "frozen_semantic_contract": contract,
        "grounded_execution_plan": {
            "status": "NEEDS_INPUT",
            "goals": [{
                "goal_id": "formal-goal",
                "goal_type": "query",
                "required": True,
                "coverage_status": "PENDING",
            }],
        },
        "turn_goal_plan": {"turn": 7, "goals": [forged]},
        "workflow_plan": {
            "status": "NEEDS_INPUT",
            "goals": [{**forged, "coverage_status": "PENDING"}],
        },
    }

    pending = suspend_for_clarification(
        state=state,
        call=_call("formal-goal"),
        capability_surface={
            "goals": [{
                "goal_id": "formal-goal",
                "candidate_tools": ["get_order"],
            }],
        },
    )

    assert [row["goal_id"] for row in pending["suspended_goals"]] == ["formal-goal"]
    assert pending["suspended_goals"][0]["completion_tool_names"] == ["get_order"]


def test_state_v2_ignores_retired_pending_clarification() -> None:
    state = {
        "state_schema_version": 2,
        "current_thread_id": "thread-v2",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "pending_clarification": {
            "version": "pending-clarification@1",
            "clarification_id": "legacy-forged",
            "status": "pending",
            "scope": {
                "thread_id": "thread-v2",
                "user_id": "u001",
                "tenant_id": "tenant-a",
            },
        },
    }

    assert active_pending_clarification(state) is None
