from __future__ import annotations

from agent_core.composition import get_runtime_registry
from agent_core.lifecycle.clarification_runtime import (
    active_pending_clarification,
    clarification_context_projection,
    continuation_tool_hints,
    suspend_for_clarification,
    transition_after_goal_declaration,
)
from agent_core.lifecycle.context_runtime import prepare_agent_loop_turn_node
from agent_core.lifecycle.goal_planning import validate_goal_declaration
from agent_core.lifecycle.protocol import planning_schemas


def _source_state() -> dict:
    return {
        "current_thread_id": "thread-clarification",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_role": "customer",
        "current_user_input": "可以退货退款吗",
        "state_schema_version": 1,
        "turn_index": 2,
        "artifact_ledger": [],
        "turn_goal_plan": {
            "turn": 2,
            "user_text": "可以退货退款吗",
            "goals": [{
                "goal_id": "refund-eligibility",
                "description": "查询目标商品能否退货退款",
                "evidence_span": "可以退货退款吗",
                "goal_type": "consult",
                "requested_effect": {"domain": "refund", "operation": "assess", "object_type": "order"},
                "required": True,
                "depends_on": [],
            }],
        },
    }


def _pending() -> dict:
    state = _source_state()
    return suspend_for_clarification(
        state=state,
        call={
            "name": "ask_user_clarification",
            "args": {
                "question": "请问是哪一件商品？",
                "reason": "有多个订单，缺少目标商品",
                "missing_kind": "target",
                "evidence_handles": ["result:orders"],
                "goal_ids": ["refund-eligibility"],
            },
        },
        capability_surface={
            "goals": [{
                "goal_id": "refund-eligibility",
                "candidate_tools": ["evaluate_refund_eligibility"],
            }],
        },
    )


def test_pending_clarification_survives_turn_reset_without_becoming_business_authority() -> None:
    state = _source_state()
    state["pending_clarification"] = _pending()

    patch = prepare_agent_loop_turn_node(state)

    assert patch["state_schema_version"] == 2
    assert "turn_goal_plan" not in patch
    assert "workflow_plan" not in patch
    assert "pending_clarification" not in patch
    assert patch["goal_blockers"][0]["goal_id"] == "refund-eligibility"
    projection = clarification_context_projection(patch)
    assert projection and projection["blockers"][0]["goal_id"] == "refund-eligibility"
    assert projection["requires_single_global_disposition"] is False
    assert "runtime_auto_select_target" not in projection


def test_pending_clarification_cannot_cross_actor_or_thread_scope() -> None:
    pending = _pending()

    assert active_pending_clarification({
        **_source_state(),
        "current_thread_id": "another-thread",
        "pending_clarification": pending,
    }) is None
    assert active_pending_clarification({
        **_source_state(),
        "current_user_id": "another-user",
        "pending_clarification": pending,
    }) is None


def test_resume_requires_explicit_resolution_and_preserves_original_goal_type() -> None:
    pending = _pending()
    state = {
        **_source_state(),
        "current_user_input": "鼠标",
        "turn_index": 3,
        "pending_clarification": pending,
    }
    args = {
        "summary": "用户用鼠标回答上一轮目标澄清",
        "clarification_resolution": {
            "clarification_id": pending["clarification_id"],
            "disposition": "resume",
            "evidence_span": "鼠标",
        },
        "goals": [{
            "goal_id": "refund-eligibility-resumed",
            "description": "查询鼠标是否可以退货退款",
            "evidence_span": "鼠标",
            "requested_effect": {"domain": "refund", "operation": "assess", "object_type": "order"},
            "goal_type": "consult",
            "required": True,
            "depends_on": [],
            "continuation_of": "refund-eligibility",
        }],
    }

    result, plan = validate_goal_declaration(
        state=state,
        args=args,
        capability_registry=get_runtime_registry().capabilities,
    )

    assert result["ok"] is True
    assert plan and plan["clarification_resolution"]["disposition"] == "resume"
    resuming = transition_after_goal_declaration(pending, plan)
    assert resuming and resuming["status"] == "resuming"
    assert continuation_tool_hints(
        {"pending_clarification": resuming}, plan["goals"]
    ) == {"refund-eligibility-resumed": ["evaluate_refund_eligibility"]}


def test_short_reply_cannot_resume_as_a_different_nearby_capability() -> None:
    pending = _pending()
    result, plan = validate_goal_declaration(
        state={
            **_source_state(),
            "current_user_input": "鼠标",
            "turn_index": 3,
            "pending_clarification": pending,
        },
        args={
            "summary": "错误地改成查询订单",
            "clarification_resolution": {
                "clarification_id": pending["clarification_id"],
                "disposition": "resume",
                "evidence_span": "鼠标",
            },
            "goals": [{
                "goal_id": "wrong-query",
                "description": "查询鼠标订单",
                "evidence_span": "鼠标",
                "requested_effect": {"domain": "order", "operation": "query", "object_type": "order"},
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "continuation_of": "refund-eligibility",
            }],
        },
        capability_registry=get_runtime_registry().capabilities,
    )

    assert result["ok"] is False
    assert plan is None
    assert "continued_requested_effect_changed:wrong-query:refund-eligibility" in result["data"]["errors"]


def test_abandon_or_new_request_clears_suspended_goal_instead_of_hijacking_it() -> None:
    pending = _pending()
    plan = {
        "turn": 3,
        "clarification_resolution": {
            "clarification_id": pending["clarification_id"],
            "disposition": "new_request",
            "evidence_span": "查发票",
        },
        "goals": [],
    }

    assert transition_after_goal_declaration(pending, plan) is None


def test_planner_schema_does_not_force_one_global_clarification_disposition() -> None:
    ordinary = planning_schemas()[0]["function"]["parameters"]
    pending = planning_schemas({"clarification_id": "clarification:test"})[0]["function"]["parameters"]

    assert "clarification_resolution" not in ordinary["required"]
    assert "clarification_resolution" not in pending["required"]
    assert "blocker_resolutions" in pending["properties"]
