from __future__ import annotations

from typing import Any

import pytest

from agent_core.composition import get_runtime_registry
from agent_core.lifecycle.dialogue_runtime import _build_loop_plan
from agent_core.lifecycle.plan_execution import begin_step_attempt, complete_step_attempt, project_grounded_execution_plan
from agent_core.lifecycle.workflow_contracts import FailureType, PlanLevel, StepStatus, WorkflowStatus
from agent_core.lifecycle.workflow_runtime import build_workflow_plan, materialize_plan_runtime, verify_workflow_for_final_answer
from tests.support.legacy_workflow_projection import mark_step_result
from tests.support.test_semantic_state import install_test_semantic_contract, requested_effect_for_tool



@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch, tmp_path):
    from agent_core.persistence.store_provider import get_store_provider, reset_store_provider_cache

    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setenv("AGENT_DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "agent.sqlite3"))
    reset_store_provider_cache()
    yield
    try:
        get_store_provider().close()
    finally:
        reset_store_provider_cache()


def _state(*, text: str, turn: int = 1) -> dict[str, Any]:
    return {
        "current_thread_id": "thread-1",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_role": "customer",
        "current_user_input": text,
        "turn_index": turn,
        "artifact_ledger": [],
        "current_turn_plan": {
            "plan_id": "turn-plan:workflow",
            "architecture": "customer_agent.runtime",
            "turn": turn,
            "effects": [],
        },
    }


def _query_args() -> dict[str, Any]:
    return {
        "goal_ids": ["legacy-goal:1"],
        "target": {"mode": "all_orders"},
        "expected_shape": "collection",
        "reference_span": "我的订单",
    }


def test_single_observation_stays_l0_direct_but_records_step_contract():
    state = _state(text="查我的订单")
    plan = _build_loop_plan(
        state,
        state["current_user_input"],
        [{"id": "call-1", "name": "list_orders", "args": _query_args()}],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=plan, user_text=state["current_user_input"])

    assert workflow["level"] == PlanLevel.DIRECT.value
    assert workflow["runtime_authority"] == "orchestration_only_not_business_fact"
    assert workflow["steps"][0]["kind"] == "observation"
    assert workflow["steps"][0]["verification"]["business_write_allowed"] is False


def test_query_then_single_draft_becomes_l1_lightweight_plan_with_dependency():
    state = _state(text="查订单后给鼠标退款")
    plan = _build_loop_plan(
        state,
        state["current_user_input"],
        [
            {"id": "query", "name": "list_orders", "args": _query_args()},
            {
                "id": "draft",
                "name": "prepare_refund",
                "args": {
                    "goal_ids": ["legacy-goal:2"],
                    "target": {"mode": "entity_match", "attribute_span": "鼠标"},
                    "reference_span": "鼠标",
                    "action_span": "退款",
                },
            },
        ],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=plan, user_text=state["current_user_input"])

    assert workflow["level"] == PlanLevel.LIGHTWEIGHT_PLAN.value
    assert workflow["status"] == WorkflowStatus.PLANNED.value
    assert len(workflow["steps"]) == 2
    assert workflow["steps"][1]["kind"] == "action_draft"
    assert workflow["steps"][1]["depends_on"] == [plan["effects"][0]["effect_id"]]
    assert "Draft" in workflow["steps"][1]["verification"]["must_cross"]
    assert workflow["steps"][1]["verification"]["business_write_allowed"] is False


def test_multi_target_action_is_l2_workflow_not_batch_authority():
    state = _state(text="把没发货的都取消，已签收的看看能不能退")
    plan = _build_loop_plan(
        state,
        state["current_user_input"],
        [
            {"id": "query", "name": "list_orders", "args": _query_args()},
            {
                "id": "cancel",
                "name": "prepare_cancel_order",
                "args": {
                    "target": {"mode": "collection", "left_handle": "result:orders:visible"},
                    "reference_span": "没发货的都",
                    "reason_code": "changed_mind",
                    "reason_span": "取消",
                },
            },
        ],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=plan, user_text=state["current_user_input"])

    assert workflow["level"] == PlanLevel.WORKFLOW.value
    assert any(reason in workflow["reasons"] for reason in ["action_on_collection_or_set", "multi_target_action_language"])
    action_steps = [step for step in workflow["steps"] if step["kind"] == "action_draft"]
    assert action_steps
    assert action_steps[0]["verification"]["business_write_allowed"] is False
    assert "ActionGateway" in action_steps[0]["verification"]["must_cross"]


def test_collection_action_stays_l2_after_terminal_call_replaces_model_tool_args():
    """A terminal response must not erase multi-target orchestration evidence."""
    state = _state(text="键盘和耳机都退了")
    plan = {
        "plan_id": "turn-plan:collection-after-terminal",
        "effects": [{
            "effect_id": "effect:refund",
            "tool_name": "prepare_refund",
            "execution_kind": "action_draft",
            "match_proof": {
                "visible_result_reference": {
                    "checks": [{"parameter_path": "target.left_handle", "expected_shape": "collection"}],
                },
            },
        }],
        # This is what remains after the model receives the rejection and ends
        # the turn. The original prepare_refund args are no longer here.
        "tool_calls": [{"name": "respond_to_user", "args": {"answer": "请逐笔选择"}}],
    }

    workflow = build_workflow_plan(state=state, turn_plan=plan, user_text=state["current_user_input"])

    assert workflow["level"] == PlanLevel.WORKFLOW.value
    assert "action_on_collection_or_set" in workflow["reasons"]


def test_workflow_step_result_updates_pause_and_final_verification():
    state = _state(text="查订单后给鼠标退款")
    install_test_semantic_contract(state, {
        "turn": 1,
        "user_text": state["current_user_input"],
        "goals": [
            {
                "goal_id": "legacy-goal:1",
                "description": "查订单",
                "evidence_span": "查订单",
                "requested_effect": requested_effect_for_tool("list_orders"),
                "required": True,
                "depends_on": [],
            },
            {
                "goal_id": "legacy-goal:2",
                "description": "给鼠标退款",
                "evidence_span": "给鼠标退款",
                "requested_effect": requested_effect_for_tool("prepare_refund"),
                "required": True,
                "depends_on": ["legacy-goal:1"],
            },
        ],
    })
    plan = _build_loop_plan(
        state,
        state["current_user_input"],
        [
            {"id": "query", "name": "list_orders", "args": _query_args()},
            {
                "id": "draft",
                "name": "prepare_refund",
                "args": {
                    "goal_ids": ["legacy-goal:2"],
                    "target": {"mode": "entity_match", "attribute_span": "鼠标"},
                    "reference_span": "鼠标",
                    "action_span": "退款",
                },
            },
        ],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=plan, user_text=state["current_user_input"])
    definition, run, projection = materialize_plan_runtime(state=state, workflow_plan=workflow)
    state.update({
        "frozen_plan_definition": definition,
        "plan_run": run,
        "grounded_execution_plan": projection,
    })
    assert verify_workflow_for_final_answer(state)["ok"] is False

    query_effect = plan["effects"][0]["effect_id"]
    run, attempt = begin_step_attempt(
        definition=state["frozen_plan_definition"], plan_run=state["plan_run"],
        effect_id=query_effect, tool_name="list_orders", args={}, execution_permit=None,
    )
    run, _ = complete_step_attempt(
        definition=state["frozen_plan_definition"], plan_run=run, attempt_id=attempt["attempt_id"],
        result={"ok": True, "code": "OK", "message": "查到订单", "runtime_outcome": {"outcome_type": "query", "effects": "none"}},
        step_status=StepStatus.SUCCEEDED.value, failure_type="NONE", verification={"verified_by_runtime": True, "goal_completion_eligible": True},
    )
    state["plan_run"] = run
    state["grounded_execution_plan"] = project_grounded_execution_plan(definition=state["frozen_plan_definition"], plan_run=run)
    assert state["grounded_execution_plan"]["steps"][0]["status"] == StepStatus.SUCCEEDED.value
    assert verify_workflow_for_final_answer(state)["ok"] is False

    draft_effect = plan["effects"][1]["effect_id"]
    run, attempt = begin_step_attempt(
        definition=state["frozen_plan_definition"], plan_run=run,
        effect_id=draft_effect, tool_name="prepare_refund", args={}, execution_permit=None,
    )
    run, _ = complete_step_attempt(
        definition=state["frozen_plan_definition"], plan_run=run, attempt_id=attempt["attempt_id"],
        result={"ok": True, "code": "OK", "message": "已创建草稿", "runtime_outcome": {"outcome_type": "draft_created", "effects": "draft_created"}},
        step_status=StepStatus.AWAITING_AUTHORIZATION.value, failure_type="NONE", verification={"verified_by_runtime": True, "goal_completion_eligible": True},
    )
    state["plan_run"] = run
    state["grounded_execution_plan"] = project_grounded_execution_plan(definition=state["frozen_plan_definition"], plan_run=run)
    assert state["grounded_execution_plan"]["steps"][1]["status"] == StepStatus.AWAITING_AUTHORIZATION.value
    assert state["grounded_execution_plan"]["status"] == WorkflowStatus.AWAITING_AUTHORIZATION.value
    assert verify_workflow_for_final_answer(state)["ok"] is True


def test_execute_loop_updates_workflow_step_status(monkeypatch):
    from agent_core.lifecycle.tool_execution_runtime import execute_agent_loop_calls_node
    from agent_modules.ecommerce.shared import context as ecommerce_context
    from tests.support.runtime_support import runtime_deps

    class QueryPort:
        def query_resources(self, _actor, *, resource_type, query_spec):
            assert resource_type == "order"
            return {
                "success": True,
                "data": [
                    {
                        "order_id": "10003",
                        "product_name": "无线鼠标",
                        "status": "待发货",
                        "amount": 99.0,
                        "version": 3,
                    }
                ],
            }

        def read_resource(self, _actor, *, resource_type, resource_id, query=None):
            return {"success": True, "data": {"order_id": str(resource_id), "product_name": "无线鼠标", "status": "待发货", "amount": 99.0, "version": 3}}

    monkeypatch.setattr(ecommerce_context, "get_business_port", lambda: QueryPort())
    from agent_core.runtime.semantic_capability_verifier import CandidateOnlySemanticVerifier

    state = _state(text="查我的订单", turn=3)
    state["semantic_capability_verifier"] = CandidateOnlySemanticVerifier()
    install_test_semantic_contract(state, {
        "turn": 3,
        "user_text": state["current_user_input"],
        "goals": [{
            "goal_id": "legacy-goal:1",
            "description": "查我的订单",
            "evidence_span": "查我的订单",
            "requested_effect": requested_effect_for_tool("list_orders"),
            "required": True,
            "depends_on": [],
        }],
    })
    plan = _build_loop_plan(
        state,
        state["current_user_input"],
        [{"id": "call-1", "name": "list_orders", "args": _query_args()}],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    state["current_turn_plan"] = plan
    workflow = build_workflow_plan(state=state, turn_plan=plan, user_text=state["current_user_input"])
    definition, run, projection = materialize_plan_runtime(state=state, workflow_plan=workflow)
    state.update({
        "frozen_plan_definition": definition,
        "plan_run": run,
        "grounded_execution_plan": projection,
    })
    deps = runtime_deps()

    output = execute_agent_loop_calls_node(
        state,
        context_bundle_builder=deps.context_bundle_builder,
        transactions=deps.transactions,
        capability_registry=deps.capability_registry,
    )

    workflow = output["grounded_execution_plan"]
    assert workflow["steps"][0]["status"] == StepStatus.SUCCEEDED.value
    assert workflow["status"] == WorkflowStatus.SUCCEEDED.value
    assert output["decision_chain"][-1]["details"]["workflow_status"] == WorkflowStatus.SUCCEEDED.value


def test_rebuilt_same_turn_workflow_carries_runtime_result_forward():
    state = _state(text="查我的订单")
    plan = _build_loop_plan(
        state,
        state["current_user_input"],
        [{"id": "call-1", "name": "list_orders", "args": _query_args()}],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    completed = mark_step_result(
        workflow_plan=build_workflow_plan(state=state, turn_plan=plan, user_text=state["current_user_input"]),
        effect_id=plan["effects"][0]["effect_id"],
        result={
            "ok": True,
            "code": "OK",
            "message": "订单观察完成",
            "runtime_outcome": {"outcome_type": "query", "effects": "none", "next_interaction": "none"},
        },
    )

    rebuilt = build_workflow_plan(
        state={**state, "grounded_execution_plan": completed},
        turn_plan=plan,
        user_text=state["current_user_input"],
    )

    assert rebuilt["workflow_id"] == completed["workflow_id"]
    assert rebuilt["steps"][0]["status"] == StepStatus.SUCCEEDED.value
    assert rebuilt["steps"][0]["result_summary"] == "订单观察完成"
    assert rebuilt["status"] == WorkflowStatus.SUCCEEDED.value


def test_submission_unknown_routes_to_reconciliation_failure_type_not_environment_retry():
    state = _state(text="提交退款")
    plan = _build_loop_plan(
        state,
        state["current_user_input"],
        [{
            "id": "refund",
            "name": "prepare_refund",
            "args": {
                "goal_ids": ["legacy-goal:1"],
                "target": {"mode": "entity_match", "attribute_span": "订单"},
                "reference_span": "订单",
                "action_span": "退款",
            },
        }],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = mark_step_result(
        workflow_plan=build_workflow_plan(state=state, turn_plan=plan, user_text=state["current_user_input"]),
        effect_id=plan["effects"][0]["effect_id"],
        result={
            "ok": False,
            "code": "SUBMISSION_UNKNOWN",
            "message": "请使用原幂等键对账。",
            "runtime_outcome": {"outcome_type": "submission_unknown", "effects": "unknown", "next_interaction": "none"},
        },
    )

    assert workflow["steps"][0]["status"] == StepStatus.SUBMISSION_UNKNOWN.value
    assert workflow["steps"][0]["failure_type"] == FailureType.SUBMISSION_UNKNOWN.value
    assert workflow["status"] == WorkflowStatus.SUBMISSION_UNKNOWN.value
