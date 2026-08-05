from __future__ import annotations

from agent_core.context.state_projection import clarification_context_projection
from agent_core.lifecycle.clarification_runtime import continuation_tool_hints, goal_blockers_for_clarification
from agent_core.lifecycle.protocol import planning_schemas
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract


def _state() -> dict:
    contract = freeze_semantic_contract(
        turn=2,
        user_text="这个能退款吗",
        summary="检查退款资格",
        goals=[{
            "goal_id": "refund-eligibility",
            "description": "检查退款资格",
            "evidence_span": "能退款吗",
            "requested_effect": {"domain": "refund", "operation": "evaluate_eligibility", "object_type": "order"},
            "required": True,
            "depends_on": [],
        }],
        alignment_proof={"verdict": "exact", "authority": "test"},
    )
    return {
        "state_schema_version": 2,
        "turn_index": 2,
        "current_user_input": "这个能退款吗",
        "frozen_semantic_contract": contract,
        "goal_blockers": [],
    }


def test_clarification_creates_goal_scoped_blocker() -> None:
    state = _state()
    blockers = goal_blockers_for_clarification(
        state=state,
        call={"name": "ask_user_clarification", "args": {
            "goal_ids": ["refund-eligibility"],
            "question": "你指的是哪个订单？",
            "reason": "缺少退款目标",
            "missing_kind": "target",
            "evidence_handles": [],
        }},
        capability_surface={"goals": [{
            "goal_id": "refund-eligibility",
            "candidate_tools": ["evaluate_refund_eligibility"],
        }]},
    )
    state["goal_blockers"] = blockers

    assert len(blockers) == 1
    assert blockers[0]["goal_id"] == "refund-eligibility"
    assert blockers[0]["completion_tool_names"] == ["evaluate_refund_eligibility"]
    projection = clarification_context_projection(state)
    assert projection and projection["requires_single_global_disposition"] is False


def test_continuation_hints_require_explicit_continuation_relation() -> None:
    state = _state()
    state["goal_blockers"] = [{
        "blocker_id": "blocker:refund-eligibility:target",
        "goal_id": "refund-eligibility",
        "status": "OPEN",
        "completion_tool_names": ["evaluate_refund_eligibility"],
    }]
    goals = [
        {"goal_id": "refund-resumed", "continuation_of": "refund-eligibility"},
        {"goal_id": "invoice-new", "continuation_of": None},
    ]

    assert continuation_tool_hints(state, goals) == {
        "refund-resumed": ["evaluate_refund_eligibility"]
    }


def test_retired_singleton_clarification_cannot_supply_continuation_hint() -> None:
    state = _state()
    state["pending_clarification"] = {
        "clarification_id": "legacy",
        "status": "resuming",
        "resume_goal_map": {"refund-resumed": "refund-eligibility"},
        "suspended_goals": [{
            "goal_id": "refund-eligibility",
            "completion_tool_names": ["evaluate_refund_eligibility"],
        }],
    }

    assert continuation_tool_hints(
        state,
        [{"goal_id": "refund-resumed", "continuation_of": "refund-eligibility"}],
    ) == {}


def test_planner_schema_exposes_goal_changes_and_blocker_resolutions_only() -> None:
    parameters = planning_schemas()[0]["function"]["parameters"]

    assert "clarification_resolution" not in parameters["properties"]
    assert "goal_changes" in parameters["properties"]
    assert "blocker_resolutions" in parameters["properties"]


def test_goal_blocker_projection_is_scoped_and_does_not_restore_singleton_authority() -> None:
    state = _state()
    blockers = goal_blockers_for_clarification(
        state=state,
        call={"name": "ask_user_clarification", "args": {
            "goal_ids": ["refund-eligibility"],
            "question": "请选择订单。",
            "reason": "目标不唯一",
            "missing_kind": "target",
            "evidence_handles": ["result:orders"],
        }},
        capability_surface={"goals": [{
            "goal_id": "refund-eligibility",
            "candidate_tools": ["evaluate_refund_eligibility"],
        }]},
    )
    state["goal_blockers"] = blockers
    state["pending_clarification"] = {"clarification_id": "forged", "status": "pending"}
    projection = clarification_context_projection(state)

    assert len(blockers) == 1
    assert blockers[0]["status"] == "OPEN"
    assert blockers[0]["missing_kind"] == "target"
    assert blockers[0]["evidence_handles"] == ["result:orders"]
    assert projection is not None
    assert projection["version"] == "goal-blocker-projection@1"
    assert projection["requires_single_global_disposition"] is False
    assert "pending_clarification" not in projection


def test_unrelated_goal_cannot_inherit_another_goals_blocker_tools() -> None:
    state = _state()
    state["goal_blockers"] = [{
        "blocker_id": "blocker:refund-eligibility:target",
        "goal_id": "refund-eligibility",
        "status": "OPEN",
        "completion_tool_names": ["evaluate_refund_eligibility"],
    }]
    hints = continuation_tool_hints(
        state,
        [
            {"goal_id": "invoice-new", "continuation_of": None},
            {"goal_id": "other-resumed", "continuation_of": "unknown-goal"},
        ],
    )

    assert hints == {}
    assert "invoice-new" not in hints
    assert "other-resumed" not in hints
    assert state["goal_blockers"][0]["goal_id"] == "refund-eligibility"
