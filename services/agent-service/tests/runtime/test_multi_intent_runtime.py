from __future__ import annotations

from typing import Any

from agent_core.composition import get_runtime_registry
from agent_core.lifecycle.dialogue_runtime import _build_loop_plan
from agent_core.lifecycle.workflow_contracts import PlanLevel, StepKind, StepStatus, WorkflowStatus
from agent_core.lifecycle.workflow_runtime import build_workflow_plan, verify_workflow_for_final_answer
from tests.support.legacy_workflow_projection import mark_step_result
from tests.support.test_semantic_state import install_test_semantic_contract, requested_effect_for_tool


def _state(text: str, *, turn: int = 1) -> dict[str, Any]:
    return {
        "current_thread_id": "thread-multi-intent",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_role": "customer",
        "current_user_input": text,
        "turn_index": turn,
        "artifact_ledger": [],
        "current_turn_plan": {
            "plan_id": "turn-plan:multi-intent",
            "architecture": "customer_agent.runtime",
            "turn": turn,
            "effects": [],
        },
    }


def _orders_target() -> dict[str, Any]:
    return {"target": {"mode": "all_orders"}, "expected_shape": "collection", "reference_span": "我的订单"}


def _visible_collection_target() -> dict[str, Any]:
    return {"mode": "collection", "left_handle": "result:orders:visible"}


def _plan(text: str, calls: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    state = _state(text)
    bound_calls = []
    goals = []
    for index, call in enumerate(calls, start=1):
        goal_id = f"goal:{index}"
        args = {**dict(call.get("args") or {}), "goal_ids": [goal_id]}
        bound_calls.append({key: value for key, value in {**call, "args": args}.items() if key != "depends_on_goal_indexes"})
        tool_name = str(call.get("name") or "")
        evidence_span = str(
            args.get("action_span")
            or args.get("reference_span")
            or args.get("request_span")
            or text
        )
        if evidence_span not in text:
            evidence_span = text
        contract = get_runtime_registry().capabilities.contract_for_tool(tool_name)
        if contract is not None and contract.execution_kind == "unsupported":
            goal_type = "unsupported"
        elif contract is not None and contract.execution_kind == "action_draft":
            goal_type = "action"
        elif tool_name == "evaluate_refund_eligibility":
            goal_type = "consult"
        else:
            goal_type = "query"
        goals.append({
            "goal_id": goal_id,
            "description": f"test goal for {tool_name}",
            "evidence_span": evidence_span,
            "goal_type": goal_type,
            "requested_effect": (
                {"domain": "open", "operation": "unsupported_test", "object_type": "request", "raw_description": evidence_span}
                if goal_type == "unsupported"
                else requested_effect_for_tool(tool_name)
            ),
            "required": True,
            "depends_on": [
                f"goal:{dependency_index}"
                for dependency_index in list(call.get("depends_on_goal_indexes") or [])
            ],
        })
    install_test_semantic_contract(state, {
        "turn": state["turn_index"],
        "user_text": text,
        "summary": "multi-goal runtime test",
        "goals": goals,
    })
    turn_plan = _build_loop_plan(state, text, bound_calls, "", capability_registry=get_runtime_registry().capabilities)
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text=text)
    return state, turn_plan, workflow


def test_multi_query_intent_keeps_both_observation_steps():
    _state_obj, turn_plan, workflow = _plan(
        "查一下我的订单，再查下物流到哪了",
        [
            {"id": "orders", "name": "list_orders", "args": _orders_target()},
            {"id": "logistics", "name": "get_order_logistics", "args": {"target": _visible_collection_target(), "query": {}}},
        ],
    )
    assert workflow["level"] == PlanLevel.LIGHTWEIGHT_PLAN.value
    assert [step["tool_name"] for step in workflow["steps"]] == ["list_orders", "get_order_logistics"]
    assert all(step["kind"] == StepKind.OBSERVATION.value for step in workflow["steps"])
    assert len(workflow["tasks"]) == 2


def test_query_plus_consultation_is_not_action_draft():
    _state_obj, _turn_plan, workflow = _plan(
        "查一下键盘订单，再看看能不能退",
        [
            {"id": "orders", "name": "list_orders", "args": _orders_target()},
            {
                "id": "eligibility",
                "name": "evaluate_refund_eligibility",
                "args": {"target": {"mode": "entity_match", "attribute_span": "键盘"}, "reference_span": "键盘", "query": {}},
            },
        ],
    )
    assert workflow["level"] == PlanLevel.LIGHTWEIGHT_PLAN.value
    assert {step["kind"] for step in workflow["steps"]} == {StepKind.OBSERVATION.value}
    assert "prepare_refund" not in [step["tool_name"] for step in workflow["steps"]]


def test_query_plus_single_write_action_keeps_dependency_and_awaits_authority():
    state, turn_plan, workflow = _plan(
        "查一下鼠标订单，然后帮我申请退款",
        [
            {"id": "orders", "name": "list_orders", "args": _orders_target()},
            {
                "id": "refund",
                "name": "prepare_refund",
                "args": {"target": {"mode": "entity_match", "attribute_span": "鼠标"}, "reference_span": "鼠标", "action_span": "申请退款"},
            },
        ],
    )
    assert workflow["level"] == PlanLevel.LIGHTWEIGHT_PLAN.value
    assert workflow["steps"][1]["depends_on"] == [turn_plan["effects"][0]["effect_id"]]
    assert workflow["tasks"][1]["depends_on"] == [workflow["tasks"][0]["task_id"]]

    workflow = mark_step_result(
        workflow_plan=workflow,
        effect_id=turn_plan["effects"][0]["effect_id"],
        result={"ok": True, "code": "OK", "message": "查到鼠标订单", "runtime_outcome": {"outcome_type": "query", "effects": "none", "next_interaction": "none"}},
    )
    workflow = mark_step_result(
        workflow_plan=workflow,
        effect_id=turn_plan["effects"][1]["effect_id"],
        result={"ok": True, "code": "OK", "message": "已创建退款草稿", "runtime_outcome": {"outcome_type": "draft_created", "effects": "draft_created", "next_interaction": "open_authority"}},
    )
    assert workflow["status"] == WorkflowStatus.AWAITING_AUTHORIZATION.value
    assert verify_workflow_for_final_answer({**state, "grounded_execution_plan": workflow})["ok"] is True


def test_multi_branch_request_requires_all_branches_before_finalizing():
    state, turn_plan, workflow = _plan(
        "把没发货的取消，已签收的看看能不能退",
        [
            {"id": "orders", "name": "list_orders", "args": _orders_target()},
            {"id": "cancel", "name": "prepare_cancel_order", "depends_on_goal_indexes": [1], "args": {"target": _visible_collection_target(), "reference_span": "没发货的", "reason_code": "changed_mind", "reason_span": "取消"}},
            {"id": "eligibility", "name": "evaluate_refund_eligibility", "depends_on_goal_indexes": [1], "args": {"target": _visible_collection_target(), "reference_span": "已签收的", "query": {}}},
        ],
    )
    assert workflow["level"] == PlanLevel.WORKFLOW.value
    assert len(workflow["steps"]) == 3
    assert {step["tool_name"] for step in workflow["steps"]} == {"list_orders", "prepare_cancel_order", "evaluate_refund_eligibility"}

    workflow = mark_step_result(
        workflow_plan=workflow,
        effect_id=turn_plan["effects"][1]["effect_id"],
        result={"ok": True, "code": "OK", "message": "已创建取消草稿", "runtime_outcome": {"outcome_type": "draft_created", "effects": "draft_created", "next_interaction": "open_authority"}},
    )
    assert workflow["status"] == WorkflowStatus.RUNNING.value
    verification = verify_workflow_for_final_answer({**state, "grounded_execution_plan": workflow})
    assert verification["ok"] is False
    assert set(verification["uncovered_goal_ids"]) == {"goal:1", "goal:3"}


def test_supported_plus_unsupported_mixed_intent_reports_unsupported_without_substitution():
    _state_obj, _turn_plan, workflow = _plan(
        "查一下物流，再告诉我快递员手机号",
        [
            {"id": "logistics", "name": "get_order_logistics", "args": {"target": {"mode": "entity_match", "attribute_span": "订单"}, "query": {}}},
            {"id": "unsupported", "name": "report_unsupported_request", "args": {"request_span": "快递员手机号"}},
        ],
    )
    assert workflow["level"] == PlanLevel.LIGHTWEIGHT_PLAN.value
    unsupported = [step for step in workflow["steps"] if step["tool_name"] == "report_unsupported_request"]
    assert unsupported
    assert unsupported[0]["kind"] == StepKind.UNSUPPORTED.value
    assert unsupported[0]["verification"]["must_not_substitute_similar_capability"] is True
