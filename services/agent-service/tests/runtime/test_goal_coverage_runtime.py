from __future__ import annotations

from tests.support.test_semantic_state import (
    install_test_plan_authority,
    install_test_semantic_contract,
    requested_effect_for_tool,
)
from agent_core.composition import get_runtime_registry
from agent_core.lifecycle.budget import compute_loop_budget
from agent_core.lifecycle.dialogue_runtime import _build_loop_plan
from agent_core.lifecycle.goal_planning import validate_goal_declaration
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract
from agent_core.lifecycle.workflow_runtime import build_workflow_plan, verify_workflow_for_final_answer
from tests.support.legacy_workflow_projection import mark_step_result


def _state(text: str) -> dict:
    return {
        "current_thread_id": "thread-goal-coverage",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_role": "customer",
        "current_user_input": text,
        "turn_index": 1,
        "artifact_ledger": [],
        "current_turn_plan": {"plan_id": "turn-plan:goal-coverage", "turn": 1, "effects": []},
    }


def _success(message: str = "ok") -> dict:
    return {
        "ok": True,
        "code": "OK",
        "message": message,
        "runtime_outcome": {
            "outcome_type": "query",
            "effects": "none",
            "next_interaction": "none",
            "safe_to_continue": True,
        },
    }


def test_missing_declared_goal_blocks_final_answer_even_when_existing_step_succeeds():
    text = "查一下订单，再查物流"
    state = _state(text)
    install_test_semantic_contract(state, {
        "version": "turn-goal-plan@1.0",
        "turn": 1,
        "user_text": text,
        "goals": [
            {"goal_id": "g1", "description": "查订单", "evidence_span": "查一下订单", "goal_type": "query", "requested_effect": requested_effect_for_tool("list_orders"), "required": True, "depends_on": [], "expected_tools": ["list_orders"]},
            {"goal_id": "g2", "description": "查物流", "evidence_span": "查物流", "goal_type": "query", "requested_effect": requested_effect_for_tool("get_order_logistics"), "required": True, "depends_on": ["g1"], "expected_tools": ["get_order_logistics"]},
        ],
    })
    turn_plan = _build_loop_plan(
        state,
        text,
        [
            {"id": "orders", "name": "list_orders", "args": {"goal_ids": ["g1"], "target": {"mode": "all_orders"}, "expected_shape": "collection", "reference_span": "查一下订单"}},
            {"id": "logistics", "name": "get_order_logistics", "args": {"goal_ids": ["g2"], "target": {"mode": "collection", "left_handle": "result:orders"}, "expected_shape": "collection", "reference_span": "查物流"}},
        ],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text=text)
    workflow = mark_step_result(workflow_plan=workflow, effect_id=turn_plan["effects"][0]["effect_id"], result=_success())
    verification = verify_workflow_for_final_answer({**state, "grounded_execution_plan": workflow})
    assert verification["ok"] is False
    assert verification["uncovered_goal_ids"] == ["g2"]
    assert workflow["goal_coverage_complete"] is False


def test_unplanned_extra_tool_is_retained_as_orphan_and_blocks_finalization():
    text = "查一下订单"
    state = _state(text)
    install_test_semantic_contract(state, {
        "version": "turn-goal-plan@1.0",
        "turn": 1,
        "user_text": text,
        "goals": [
            {"goal_id": "g1", "description": "查订单", "evidence_span": text, "goal_type": "query", "requested_effect": requested_effect_for_tool("list_orders"), "required": True, "depends_on": [], "expected_tools": ["list_orders"]},
        ],
    })
    calls = [
        {"id": "orders", "name": "list_orders", "args": {"goal_ids": ["g1"], "target": {"mode": "all_orders"}, "expected_shape": "collection", "reference_span": text}},
        {"id": "logistics", "name": "get_order_logistics", "args": {"target": {"mode": "collection", "left_handle": "result:visible"}, "expected_shape": "collection", "reference_span": text}},
    ]
    turn_plan = _build_loop_plan(state, text, calls, "", capability_registry=get_runtime_registry().capabilities)
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text=text)
    for effect in turn_plan["effects"]:
        workflow = mark_step_result(workflow_plan=workflow, effect_id=effect["effect_id"], result=_success())
    verification = verify_workflow_for_final_answer({**state, "grounded_execution_plan": workflow})
    assert verification["ok"] is False
    assert verification["reason"] == "plan_validation_rejected"
    assert {row["code"] for row in verification["errors"]} >= {
        "PLAN_EXACT_GOAL_BINDING_REQUIRED",
        "PLAN_EXACT_CAPABILITY_ROLE_REQUIRED",
    }
    assert any(task["task_id"].startswith("task:orphan:") for task in workflow["tasks"])


def test_loop_budget_stays_open_while_a_required_goal_is_uncovered():
    state = _state("查订单和物流")
    install_test_plan_authority(
        state,
        goals=[{"goal_id": "g2", "required": True}],
    )
    state["tool_trace"] = [{
        "name": "list_orders",
        "classification": "observation",
        "result": _success(),
    }]
    budget = compute_loop_budget(state)
    assert budget.terminal_only is False
    assert budget.reason == "workflow_goal_or_step_incomplete"


def test_verified_audit_result_closes_business_requery_and_requires_history_response():
    state = _state("刚才我要给哪个订单开票？")
    install_test_plan_authority(
        state,
        goals=[{"goal_id": "recall", "required": True}],
    )
    state["tool_trace"] = [
        {"name": "declare_turn_goals", "classification": "internal", "result": {"ok": True}},
        {
            "name": "inspect_audit_event",
            "classification": "internal",
            "result": {
                "ok": True,
                "data": {
                    "historical_only": True,
                    "answer_summary": "订单10004可以申请电子发票。",
                    "result_handles": ["h_result:invoice-10004"],
                },
            },
        },
    ]

    budget = compute_loop_budget(state)

    assert budget.terminal_only is True
    assert budget.reason == "verified_history_recall_ready"


def test_partial_multi_history_audit_does_not_prematurely_force_terminal_response():
    state = _state("比较键盘和杯子的退款结论")
    install_test_plan_authority(
        state,
        goals=[{"goal_id": "compare", "required": True}],
    )
    state["tool_trace"] = [
        {
            "name": "inspect_audit_event",
            "classification": "internal",
            "args": {"trace_handle": "trace:keyboard"},
            "result": {"ok": False, "code": "AUDIT_REASON_NOT_IN_CURRENT_TURN"},
        },
        {
            "name": "inspect_audit_event",
            "classification": "internal",
            "args": {"trace_handle": "trace:cup"},
            "result": {"ok": True, "data": {
                "trace_handle": "trace:cup",
                "historical_only": True,
                "answer_summary": "杯子退款结论",
                "result_handles": ["h_order:cup"],
            }},
        },
    ]

    budget = compute_loop_budget(state)

    assert budget.terminal_only is False
    assert budget.reason == "workflow_goal_or_step_incomplete"


def test_repaired_multi_history_audits_bind_all_released_evidence() -> None:
    from agent_core.lifecycle.dialogue_runtime import _bind_verified_history_recall_evidence

    state = _state("比较键盘和杯子的退款结论")
    install_test_plan_authority(
        state,
        goals=[{"goal_id": "compare", "required": True}],
    )
    state["tool_trace"] = [
        {
            "name": "inspect_audit_event",
            "classification": "internal",
            "args": {"trace_handle": "trace:keyboard"},
            "result": {"ok": False, "code": "AUDIT_REASON_NOT_IN_CURRENT_TURN"},
        },
        {
            "name": "inspect_audit_event",
            "classification": "internal",
            "args": {"trace_handle": "trace:cup"},
            "result": {"ok": True, "data": {
                "trace_handle": "trace:cup",
                "historical_only": True,
                "answer_summary": "杯子退款结论",
                "result_handles": ["h_order:cup"],
            }},
        },
        {
            "name": "inspect_audit_event",
            "classification": "internal",
            "args": {"trace_handle": "trace:keyboard"},
            "result": {"ok": True, "data": {
                "trace_handle": "trace:keyboard",
                "historical_only": True,
                "answer_summary": "键盘退款结论",
                "result_handles": ["h_order:keyboard"],
            }},
        },
    ]
    calls = [{
        "id": "compare-answer",
        "name": "respond_to_user",
        "args": {"answer": "比较结果", "evidence_handles": []},
    }]

    bound, proof = _bind_verified_history_recall_evidence(state, calls)

    assert compute_loop_budget(state).reason == "verified_history_recall_ready"
    assert bound[0]["args"]["evidence_handles"] == ["h_order:keyboard", "h_order:cup"] or bound[0]["args"]["evidence_handles"] == ["h_order:cup", "h_order:keyboard"]
    assert set(proof["source_trace_handles"]) == {"trace:keyboard", "trace:cup"}


def test_verified_history_recall_binds_only_the_response_protocol():
    from langchain_core.messages import HumanMessage

    from agent_core.context import ContextBundleBuilder
    from agent_core.lifecycle.dialogue_runtime import agent_loop_node
    from agent_core.model_calls import model_call_scope
    from tests.support.scripted_chat_model import ScriptedChatModel

    class Transactions:
        def list_drafts_for_scope(self, **_kwargs):
            return []

    text = "刚才我要给哪个订单开票？"
    state = _state(text)
    from agent_core.ledger import result_entry

    visible = result_entry(
        capability="invoice.policy",
        member_handles=["h_order:10004"],
        labels=["定制马克杯（订单10004）"],
        scope={"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-goal-coverage"},
        turn=1,
        source_target={"target": {"mode": "entity_match"}},
        handle="h_result:invoice-10004",
    )
    visible["presentation_origin"] = {
        "origin": "customer_final_response",
        "source_turn": 1,
        "source_result_handle": visible["handle"],
        "source_effect_id": "effect:invoice",
    }
    state.update({
        "messages": [HumanMessage(content=text)],
        "agent_loop_step": 2,
        "agent_loop_max_steps": 6,
        "answer_protocol_retry": 0,
        "artifact_ledger": [visible],
        "tool_trace": [
            {"name": "declare_turn_goals", "classification": "internal", "result": {"ok": True}},
            {
                "name": "inspect_audit_event",
                "classification": "internal",
                "result": {
                    "ok": True,
                    "data": {
                        "historical_only": True,
                        "answer_summary": "订单10004可以申请电子发票。",
                        "result_handles": ["h_result:invoice-10004"],
                    },
                },
            },
        ],
        "task_board": [],
        "loop_plans": [],

        "grounded_execution_plan": {
            "status": "RUNNING",
            "goals": [{
                "goal_id": "recall",
                "goal_type": "query",
                "required": True,
                "coverage_status": "PENDING",
                "covered_by_step_ids": [],
                "covered_by_terminal_tools": [],
            }],
            "steps": [],
        },
    })
    install_test_semantic_contract(state, {
            "version": "turn-goal-plan@1.0",
            "turn": 1,
            "user_text": text,
            "goals": [{
                "goal_id": "recall",
                "description": "确认刚才开票的订单",
                "evidence_span": text,
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "expected_tools": [],
            }],
        })
    model = ScriptedChatModel([{
        "tool_calls": [{
            "id": "recall-answer",
            "name": "respond_to_user",
            "args": {
                "goal_ids": ["recall"],
                "answer": "刚才要开票的是订单10004。",
                "evidence_handles": [],
            },
        }],
    }])

    with model_call_scope(max_calls=1, scope="history-recall-terminal-binding"):
        update = agent_loop_node(
            state,
            context_bundle_builder=ContextBundleBuilder(transactions=Transactions()),
            capability_registry=get_runtime_registry().capabilities,
            model_resolver=lambda: model,
        )

    assert model.bound_tool_choices == ["required"]
    assert model.bound_tool_history[-1] == {"respond_to_user"}
    assert update["status"] == "GroundedFinalAnswer"
    assert update["current_final_answer"] == "刚才要开票的是订单10004。"
    assert update["answer_evidence_handles"] == ["h_result:invoice-10004"]
    assert update["history_recall_evidence_binding"]["value_invention_allowed"] is False


def test_turn_audit_persists_only_released_answer_evidence_for_history_recall():
    from agent_core.context.audit_inspection import build_audit_index, inspect_audit_event
    from agent_core.observability.audit_turn_trace import build_turn_event

    event = build_turn_event(
        plan={"plan_id": "plan:invoice", "tool_calls": []},
        turn=9,
        user_text="订单10004能开发票吗？",
        tool_trace=[],
        answer="订单10004可以开发票。",
        status="GroundedFinalAnswer",
        answer_evidence_handles=["h_result:invoice-10004"],
    )
    index = build_audit_index([event])
    assert index[0]["result_handles"] == ["h_result:invoice-10004"]

    result = inspect_audit_event(
        {
            "current_user_input": "刚才我要给哪个订单开票？",
            "conversation_event_log": [event],
            "context_bundle": {"omitted_context_audit": {"audit_index": index}},
        },
        trace_handle=event["event_id"],
        reason_span="刚才",
    )

    assert result["ok"] is True
    assert result["data"]["result_handles"] == ["h_result:invoice-10004"]


def test_successful_prerequisite_read_does_not_complete_a_consultation_goal():
    """A successful step is not automatically the requested user outcome.

    Order details may be a useful prerequisite for an invoice consultation,
    but the consultation remains open until a capability whose contract
    explicitly closes ``consult`` has succeeded.
    """
    text = "订单10004能开发票吗？我只问发票，不要退款，也不要售后。"
    state = _state(text)
    install_test_semantic_contract(state, {
        "version": "turn-goal-plan@1.0",
        "turn": 1,
        "user_text": text,
        "goals": [{
            "goal_id": "invoice-consult",
            "description": "咨询订单10004的发票政策",
            "requested_effect": requested_effect_for_tool("consult_invoice_policy"),
            "evidence_span": "订单10004能开发票吗",
            "goal_type": "consult",
            "required": True,
            "depends_on": [],
            "expected_tools": [],
        }],
    })
    turn_plan = _build_loop_plan(
        state,
        text,
        [{
            "id": "details-prerequisite",
            "name": "get_order_details",
            "args": {
                "goal_ids": ["invoice-consult"],
                "target": {"mode": "entity_match", "attribute_span": "订单10004"},
                "reference_span": "订单10004",
            },
        }],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text=text)
    workflow = mark_step_result(
        workflow_plan=workflow,
        effect_id=turn_plan["effects"][0]["effect_id"],
        result=_success("订单详情读取成功"),
    )

    goal = workflow["goals"][0]
    assert goal["coverage_status"] == "PENDING"
    assert goal["covered_by_step_ids"] == []
    assert workflow["steps"][0]["verification"]["goal_completion_eligible"] is False
    assert verify_workflow_for_final_answer({**state, "grounded_execution_plan": workflow})["ok"] is False

    budget = compute_loop_budget({
        **state,
        "grounded_execution_plan": workflow,
        "tool_trace": [{
            "name": "get_order_details",
            "classification": "observation",
            "result": _success("订单详情读取成功"),
        }],
    })
    assert budget.terminal_only is False
    assert budget.reason == "workflow_goal_or_step_incomplete"


def test_declared_consultation_capability_completes_the_consultation_goal():
    text = "订单10004能开发票吗？"
    state = _state(text)
    install_test_semantic_contract(state, {
        "version": "turn-goal-plan@1.0",
        "turn": 1,
        "user_text": text,
        "goals": [{
            "goal_id": "invoice-consult",
            "description": "咨询订单10004的发票政策",
            "requested_effect": requested_effect_for_tool("consult_invoice_policy"),
            "evidence_span": text,
            "goal_type": "consult",
            "required": True,
            "depends_on": [],
            "expected_tools": [],
        }],
    })
    turn_plan = _build_loop_plan(
        state,
        text,
        [{
            "id": "invoice-policy",
            "name": "consult_invoice_policy",
            "args": {
                "goal_ids": ["invoice-consult"],
                "target": {"mode": "entity_match", "attribute_span": "订单10004"},
                "reference_span": "订单10004",
                "issue_span": "发票",
                "question_span": text,
            },
        }],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text=text)
    workflow = mark_step_result(
        workflow_plan=workflow,
        effect_id=turn_plan["effects"][0]["effect_id"],
        result=_success("发票政策咨询成功"),
    )

    assert workflow["goals"][0]["coverage_status"] == "COVERED"
    assert workflow["goals"][0]["covered_by_step_ids"] == ["step:1"]
    assert workflow["steps"][0]["verification"]["goal_completion_eligible"] is True
    assert verify_workflow_for_final_answer({**state, "grounded_execution_plan": workflow})["ok"] is True


def test_single_result_goal_is_not_completed_by_sorting_a_multi_member_collection():
    text = "这两个里面最贵的是哪个？"
    state = _state(text)
    install_test_semantic_contract(state, {
        "version": "turn-goal-plan@1.1",
        "turn": 1,
        "user_text": text,
        "goals": [{
            "goal_id": "most-expensive",
            "description": "找出两个订单中最贵的那一个",
            "evidence_span": "最贵的是哪个",
            "goal_type": "query",
            "expected_result_cardinality": "single",
            "required": True,
            "depends_on": [],
            "expected_tools": [],
        }],
    })
    turn_plan = _build_loop_plan(
        state,
        text,
        [{
            "id": "sort-only",
            "name": "list_orders",
            "args": {
                "goal_ids": ["most-expensive"],
                "target": {
                    "mode": "set_operation",
                    "operator": "sort",
                    "left_handle": "result:two-orders",
                    "sort_field": "amount",
                    "sort_direction": "desc",
                    "sort_span": "最贵",
                },
                "expected_shape": "collection",
                "reference_span": "最贵的是哪个",
            },
        }],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text=text)
    workflow = mark_step_result(
        workflow_plan=workflow,
        effect_id=turn_plan["effects"][0]["effect_id"],
        result=_success("排序完成，共2项"),
    )

    verification = workflow["steps"][0]["verification"]
    assert verification["expected_result_cardinality"] == "single"
    assert verification["effect_result_cardinality_hint"] == "collection"
    assert verification["goal_cardinality_eligible"] is False
    assert verification["goal_completion_eligible"] is False
    assert workflow["goals"][0]["coverage_status"] == "PENDING"
    assert verify_workflow_for_final_answer({**state, "grounded_execution_plan": workflow})["ok"] is False


def test_single_result_goal_accepts_verified_singleton_collection_population():
    text = "剩下这个订单现在什么状态？"
    state = _state(text)
    install_test_semantic_contract(state, {
        "version": "turn-goal-plan@1.1",
        "turn": 1,
        "user_text": text,
        "goals": [{
            "goal_id": "remaining-order",
            "description": "查询剩下这一个订单的状态",
            "requested_effect": requested_effect_for_tool("list_orders"),
            "evidence_span": "剩下这个订单",
            "goal_type": "query",
            "expected_result_cardinality": "single",
            "required": True,
            "depends_on": [],
            "expected_tools": [],
        }],
    })
    turn_plan = _build_loop_plan(
        state,
        text,
        [{
            "id": "singleton-collection",
            "name": "list_orders",
            "args": {
                "goal_ids": ["remaining-order"],
                "target": {"mode": "collection", "left_handle": "result:remaining-one"},
                "expected_shape": "collection",
                "reference_span": "剩下这个订单",
            },
        }],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text=text)
    workflow = mark_step_result(
        workflow_plan=workflow,
        effect_id=turn_plan["effects"][0]["effect_id"],
        result={**_success("已找到1笔订单"), "data": {"count": 1, "orders": [{"order_id": "10002"}]}},
    )

    verification = workflow["steps"][0]["verification"]
    assert verification["verified_result_member_count"] == 1
    assert verification["goal_cardinality_eligible"] is True
    assert verification["goal_completion_eligible"] is True
    assert workflow["goals"][0]["coverage_status"] == "COVERED"


def test_single_conclusion_accepts_one_member_target_proved_by_match_proof():
    text = "现在改成查退款资格"
    state = _state(text)
    install_test_semantic_contract(state, {
        "version": "turn-goal-plan@1.1",
        "turn": 1,
        "user_text": text,
        "goals": [{
            "goal_id": "refund-eligibility",
            "description": "查询上一订单的退款资格",
            "requested_effect": requested_effect_for_tool("evaluate_refund_eligibility"),
            "evidence_span": "查退款资格",
            "goal_type": "consult",
            "expected_result_cardinality": "single",
            "required": True,
            "depends_on": [],
            "expected_tools": [],
        }],
    })
    turn_plan = _build_loop_plan(
        state,
        text,
        [{
            "id": "eligibility",
            "name": "evaluate_refund_eligibility",
            "args": {
                "goal_ids": ["refund-eligibility"],
                "target": {"mode": "collection", "left_handle": "result:one-order"},
                "reference_span": "查退款资格",
                "question_span": "查退款资格",
                "reason_span": "查退款资格",
            },
        }],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    turn_plan["effects"][0]["match_proof"] = {
        "visible_result_reference": {
            "checks": [{
                "valid": True,
                "validated_ref": {
                    "shape": "collection",
                    "member_handles": ["h_order:10004"],
                },
            }],
        },
    }
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text=text)
    workflow = mark_step_result(
        workflow_plan=workflow,
        effect_id=turn_plan["effects"][0]["effect_id"],
        result={
            **_success("需要补充问题类型"),
            "data": {"preview": {"decision": "NEEDS_INPUT", "required_inputs": [{"name": "reason_code"}]}},
        },
    )

    verification = workflow["steps"][0]["verification"]
    assert verification["verified_result_member_count"] is None
    assert verification["verified_target_member_count"] == 1
    assert verification["goal_cardinality_eligible"] is True
    assert verification["goal_completion_eligible"] is True
    assert workflow["goals"][0]["coverage_status"] == "COVERED"


def test_existential_record_goal_accepts_verified_empty_collection_population():
    """A conclusive empty record lookup is not an incomplete single selection."""
    text = "它有没有发票记录？"
    state = _state(text)
    install_test_semantic_contract(state, {
        "version": "turn-goal-plan@1.1",
        "turn": 1,
        "user_text": text,
        "goals": [{
            "goal_id": "invoice-records",
            "description": "核验上一订单是否存在发票记录",
            "requested_effect": requested_effect_for_tool("list_invoices"),
            "evidence_span": "它有没有发票记录",
            "goal_type": "query",
            "expected_result_cardinality": "collection",
            "required": True,
            "depends_on": [],
            "expected_tools": [],
        }],
    })
    turn_plan = _build_loop_plan(
        state,
        text,
        [{
            "id": "empty-invoice-records",
            "name": "list_invoices",
            "args": {
                "goal_ids": ["invoice-records"],
                "target": {"mode": "collection", "left_handle": "result:visible-order"},
                "expected_shape": "collection",
                "reference_span": "它",
            },
        }],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text=text)
    workflow = mark_step_result(
        workflow_plan=workflow,
        effect_id=turn_plan["effects"][0]["effect_id"],
        result={**_success("没有发票记录"), "data": {"count": 0, "items": []}},
    )

    verification = workflow["steps"][0]["verification"]
    assert verification["verified_result_member_count"] == 0
    assert verification["goal_cardinality_eligible"] is True
    assert verification["goal_completion_eligible"] is True
    assert workflow["goals"][0]["coverage_status"] == "COVERED"
    assert verify_workflow_for_final_answer({**state, "grounded_execution_plan": workflow})["ok"] is True


def test_verified_clarification_blocks_the_bound_goal_without_marking_it_complete():
    text = "可以退货退款吗？"
    state = _state(text)
    state.update({
        "state_schema_version": 2,
        "frozen_semantic_contract": freeze_semantic_contract(
            turn=1,
            user_text=text,
            summary=text,
            goals=[{
                "goal_id": "g1",
                "description": "咨询退货退款政策",
                "evidence_span": text,
                "requested_effect": {
                    "domain": "refund",
                    "operation": "consult_policy",
                    "object_type": "order",
                },
                "required": True,
                "depends_on": [],
            }],
            alignment_proof={"verdict": "exact", "source": "test"},
        ),
    })
    turn_plan = _build_loop_plan(
        state,
        text,
        [{
            "id": "clarify-order",
            "name": "ask_user_clarification",
            "args": {
                "goal_ids": ["g1"],
                "question": "请问您想咨询哪一笔订单？",
                "reason": "存在多个可见订单",
                "missing_kind": "target",
                "evidence_handles": [],
            },
        }],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text=text)

    by_goal = {goal["goal_id"]: goal for goal in workflow["goals"]}
    assert by_goal["g1"]["coverage_status"] == "BLOCKED"
    assert by_goal["g1"]["covered_by_terminal_tools"] == ["ask_user_clarification"]
    assert workflow["goal_coverage_complete"] is True
    assert workflow["status"] == "NEEDS_INPUT"
    verification = verify_workflow_for_final_answer({**state, "grounded_execution_plan": workflow})
    assert verification["ok"] is True
    assert verification["reason"] == "clarification_pause"
    assert verification["suspended_goal_ids"] == ["g1"]


def test_clarification_can_pause_the_bound_consult_goal_without_a_fake_user_goal():
    """Clarification is a runtime pause, not an outcome the user requested.

    Goal Alignment must not force the model to invent a second user goal just
    because an otherwise valid consultation needs target selection.
    """
    text = "可以退货退款吗？"
    state = _state(text)
    install_test_semantic_contract(state, {
        "version": "turn-goal-plan@1.0",
        "turn": 1,
        "user_text": text,
        "goals": [{
            "goal_id": "refund-consult",
            "description": "咨询退货退款政策",
            "evidence_span": text,
            "goal_type": "consult",
            "required": True,
            "depends_on": [],
            "expected_tools": [],
        }],
    })
    turn_plan = _build_loop_plan(
        state,
        text,
        [{
            "id": "clarify-refund-order",
            "name": "ask_user_clarification",
            "args": {
                "goal_ids": ["refund-consult"],
                "question": "请问您想咨询哪一笔订单？",
                "reason": "存在多笔可见订单，目标不唯一",
                "evidence_handles": ["h_result:visible-orders"],
            },
        }],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )

    workflow = build_workflow_plan(state=state, turn_plan=turn_plan, user_text=text)
    goal = workflow["goals"][0]
    assert goal["coverage_status"] == "BLOCKED"
    assert goal["covered_by_terminal_tools"] == ["ask_user_clarification"]
    assert goal["satisfaction_proof"]["kind"] == "clarification_pause"
    assert workflow["status"] == "NEEDS_INPUT"

    verification = verify_workflow_for_final_answer({**state, "grounded_execution_plan": workflow})
    assert verification["ok"] is True
    assert verification["reason"] == "clarification_pause"
    assert verification["suspended_goal_ids"] == ["refund-consult"]


def test_agent_loop_releases_verified_clarification_as_canonical_outcome():
    from langchain_core.messages import HumanMessage

    from agent_core.context import ContextBundleBuilder
    from agent_core.lifecycle.dialogue_runtime import agent_loop_node
    from agent_core.model_calls import model_call_scope
    from tests.support.scripted_chat_model import ScriptedChatModel

    class Transactions:
        def list_drafts_for_scope(self, **_kwargs):
            return []

    text = "可以退货退款吗？"
    state = _state(text)
    state.update({
        "state_schema_version": 2,
        "messages": [HumanMessage(content=text)],
        "agent_loop_step": 1,
        "agent_loop_max_steps": 6,
        "answer_protocol_retry": 0,
        "tool_trace": [{"name": "declare_turn_goals", "result": {"ok": True}}],
        "task_board": [],
        "loop_plans": [],
        "frozen_semantic_contract": freeze_semantic_contract(
            turn=1,
            user_text=text,
            summary=text,
            goals=[{
                "goal_id": "g1",
                "description": "咨询退货退款政策",
                "evidence_span": text,
                "requested_effect": {
                    "domain": "refund",
                    "operation": "consult_policy",
                    "object_type": "order",
                },
                "required": True,
                "depends_on": [],
            }],
            alignment_proof={"verdict": "exact", "source": "test"},
        ),
    })
    model = ScriptedChatModel([{
        "tool_calls": [{
            "id": "clarify-order",
            "name": "ask_user_clarification",
            "args": {
                "goal_ids": ["g1"],
                "question": "请问您想咨询哪一笔订单？",
                "reason": "存在多个可见订单",
                "missing_kind": "target",
                "evidence_handles": [],
            },
        }],
    }])

    with model_call_scope(max_calls=1, scope="clarification-outcome-regression"):
        update = agent_loop_node(
            state,
            context_bundle_builder=ContextBundleBuilder(transactions=Transactions()),
            capability_registry=get_runtime_registry().capabilities,
            model_resolver=lambda: model,
        )

    assert not update.get("tool_error"), update.get("tool_error")
    assert update["current_final_answer"] == "请问您想咨询哪一笔订单？", update
    assert update["runtime_outcome"]["outcome_type"] == "clarification"
    assert update["runtime_outcome"]["next_interaction"] == "need_selection"
    assert "pending_clarification" not in update
    assert [row["goal_id"] for row in update["goal_blockers"]] == ["g1"]
    assert update["goal_blockers"][0]["missing_kind"] == "target"


def test_goal_declaration_phase_requires_provider_tool_call():
    from langchain_core.messages import HumanMessage

    from agent_core.context import ContextBundleBuilder
    from agent_core.lifecycle.dialogue_runtime import agent_loop_node
    from agent_core.model_calls import model_call_scope
    from tests.support.scripted_chat_model import ScriptedChatModel

    class Transactions:
        def list_drafts_for_scope(self, **_kwargs):
            return []

    text = "回到刚才的蓝牙耳机，它是哪一个订单？"
    state = _state(text)
    state.update({
        "messages": [HumanMessage(content=text)],
        "agent_loop_step": 0,
        "agent_loop_max_steps": 6,
        "task_board": [],
        "loop_plans": [],
    })
    model = ScriptedChatModel([{
        "tool_calls": [{
            "id": "declare-return-goal",
            "name": "declare_turn_goals",
            "args": {
                "summary": text,
                "goals": [{
                    "goal_id": "g1",
                    "description": "查询蓝牙耳机订单号",
                    "evidence_span": "蓝牙耳机",
                    "goal_type": "query",
                    "required": True,
                    "depends_on": [],
                }],
            },
        }],
    }])

    with model_call_scope(max_calls=1, scope="goal-declaration-required-call"):
        update = agent_loop_node(
            state,
            context_bundle_builder=ContextBundleBuilder(transactions=Transactions()),
            capability_registry=get_runtime_registry().capabilities,
            model_resolver=lambda: model,
        )

    assert model.bound_tool_choices == ["declare_turn_goals"]
    assert model.bound_tool_history == [{"declare_turn_goals"}]
    assert update["status"] == "AgentNextStepPlanned"
    assert [call["name"] for call in update["current_turn_plan"]["tool_calls"]] == ["declare_turn_goals"]


def test_goal_declaration_protocol_is_exactly_forced_and_bounded() -> None:
    from langchain_core.messages import HumanMessage

    from agent_core.context import ContextBundleBuilder
    from agent_core.lifecycle.dialogue_runtime import agent_loop_node
    from agent_core.model_calls import model_call_scope
    from tests.support.scripted_chat_model import ScriptedChatModel

    class Transactions:
        def list_drafts_for_scope(self, **_kwargs):
            return []

    state = _state("刚才我要给哪个订单开票？")
    state.update({
        "messages": [HumanMessage(content="刚才我要给哪个订单开票？")],
        "agent_loop_step": 0,
        "agent_loop_max_steps": 6,
        "goal_declaration_retry": 0,
    })
    model = ScriptedChatModel([
        {"content": "刚才要开票的是订单10004。"},
        {"content": "刚才要开票的是订单10004。"},
    ])

    with model_call_scope(max_calls=2, scope="goal-declaration-bounded-retry"):
        first = agent_loop_node(
            state,
            context_bundle_builder=ContextBundleBuilder(transactions=Transactions()),
            capability_registry=get_runtime_registry().capabilities,
            model_resolver=lambda: model,
        )
        second = agent_loop_node(
            {**state, **first},
            context_bundle_builder=ContextBundleBuilder(transactions=Transactions()),
            capability_registry=get_runtime_registry().capabilities,
            model_resolver=lambda: model,
        )

    assert model.bound_tool_choices == ["declare_turn_goals", "declare_turn_goals"]
    assert first["status"] == "GoalDeclarationProtocolRetry"
    assert second["status"] == "GoalDeclarationUnavailable"
    assert second["phase"] == "final"
    assert second["agent_loop_step"] == 2
    assert second["goal_declaration_retry"] == 2
    assert len(second["debug_llm_calls"]) == 2


def test_unique_latest_visible_scope_rejects_invented_clarification() -> None:
    from langchain_core.messages import HumanMessage

    from agent_core.lifecycle.dialogue_runtime import _unnecessary_unique_scope_clarification

    state = {
        "messages": [HumanMessage(content="其中最贵的是哪个？")],
        "context_bundle": {
            "visible_result_refs": [
                {
                    "result_ref": "h:latest-one",
                    "is_latest_visible_turn": True,
                    "member_handles": ["h:order:10001"],
                },
                {
                    "result_ref": "h:older-four",
                    "is_latest_visible_turn": False,
                    "member_handles": ["h:order:10001", "h:order:10002"],
                },
            ],
        },
    }

    # Runtime no longer infers pronouns or implicit reference from raw text.
    # Without a model-declared target/scope gap there is no program semantic
    # decision to reject.
    assert _unnecessary_unique_scope_clarification(state) is None

    conflict = _unnecessary_unique_scope_clarification(
        state,
        {"name": "ask_user_clarification", "args": {"missing_kind": "scope"}},
    )
    assert conflict == {
        "reason_code": "unique_latest_visible_scope",
        "reference_mode": "model_declared_target_gap",
        "rejected_missing_kind": "scope",
        "member_handle": "h:order:10001",
        "latest_result_refs": ["h:latest-one"],
    }


def test_unique_latest_scope_rejects_zero_anaphora_target_clarification_only() -> None:
    from langchain_core.messages import HumanMessage

    from agent_core.lifecycle.dialogue_runtime import _unnecessary_unique_scope_clarification

    state = {
        "messages": [HumanMessage(content="可以退货退款吗？")],
        "context_bundle": {
            "visible_result_refs": [{
                "result_ref": "h:latest-one",
                "is_latest_visible_turn": True,
                "member_handles": ["h:order:10001"],
            }],
        },
    }
    target_call = {
        "name": "ask_user_clarification",
        "args": {"missing_kind": "target"},
    }
    condition_call = {
        "name": "ask_user_clarification",
        "args": {"missing_kind": "condition"},
    }

    conflict = _unnecessary_unique_scope_clarification(state, target_call)

    assert conflict == {
        "reason_code": "unique_latest_visible_scope",
        "reference_mode": "model_declared_target_gap",
        "rejected_missing_kind": "target",
        "member_handle": "h:order:10001",
        "latest_result_refs": ["h:latest-one"],
    }
    assert _unnecessary_unique_scope_clarification(state, condition_call) is None


def test_plain_content_protocol_retry_forces_a_bound_terminal_tool_call():
    from langchain_core.messages import HumanMessage

    from agent_core.context import ContextBundleBuilder
    from agent_core.lifecycle.dialogue_runtime import agent_loop_node
    from agent_core.lifecycle.protocol import TERMINAL_TOOL_NAMES
    from agent_core.model_calls import model_call_scope
    from tests.support.scripted_chat_model import ScriptedChatModel

    class Transactions:
        def list_drafts_for_scope(self, **_kwargs):
            return []

    text = "可以退货退款吗？"
    state = _state(text)
    state.update({
        "messages": [HumanMessage(content=text)],
        "agent_loop_step": 1,
        "agent_loop_max_steps": 6,
        "answer_protocol_retry": 0,
        "tool_trace": [{"name": "declare_turn_goals", "result": {"ok": True}}],
        "task_board": [],
        "loop_plans": [],

    })
    install_test_semantic_contract(state, {
            "version": "turn-goal-plan@1.0",
            "turn": 1,
            "user_text": text,
            "goals": [{
                "goal_id": "g1",
                "description": "澄清具体退款订单",
                "evidence_span": text,
                "goal_type": "clarification",
                "required": True,
                "depends_on": [],
                "expected_tools": [],
            }],
        })
    model = ScriptedChatModel([
        {"content": "请问您具体想咨询哪一笔订单？"},
        {"tool_calls": [{
            "id": "clarify-order",
            "name": "ask_user_clarification",
            "args": {
                "goal_ids": ["g1"],
                "question": "请问您具体想咨询哪一笔订单？",
                "reason": "当前可见集合包含多笔订单",
                "evidence_handles": [],
            },
        }]},
    ])

    with model_call_scope(max_calls=2, scope="terminal-tool-choice-regression"):
        first = agent_loop_node(
            state,
            context_bundle_builder=ContextBundleBuilder(transactions=Transactions()),
            capability_registry=get_runtime_registry().capabilities,
            model_resolver=lambda: model,
        )
        assert first["status"] == "WorkflowIncompleteRetry"
        assert first["decision_chain"][-1]["decision"] == "plain_prose_rejected_for_incomplete_workflow"

        second = agent_loop_node(
            {**state, **first},
            context_bundle_builder=ContextBundleBuilder(transactions=Transactions()),
            capability_registry=get_runtime_registry().capabilities,
            model_resolver=lambda: model,
        )

    assert model.bound_tool_choices == [None, "required"]
    assert model.bound_tool_history[-1] == {"ask_user_clarification"}
    assert second["status"] == "GeneralFinalAnswer"
    assert second["current_final_answer"] == "请问您具体想咨询哪一笔订单？"
    assert second["runtime_outcome"]["outcome_type"] == "clarification"


def test_plain_prose_with_pending_query_reopens_only_goal_completion_capability() -> None:
    from langchain_core.messages import HumanMessage

    from agent_core.context import ContextBundleBuilder
    from agent_core.lifecycle.dialogue_runtime import agent_loop_node
    from agent_core.model_calls import model_call_scope
    from tests.support.scripted_chat_model import ScriptedChatModel

    class Transactions:
        def list_drafts_for_scope(self, **_kwargs):
            return []

    text = "其中最贵的是哪个？"
    state = _state(text)
    state.update({
        "messages": [HumanMessage(content=text)],
        "agent_loop_step": 1,
        "agent_loop_max_steps": 6,
        "answer_protocol_retry": 0,
        "tool_trace": [{"name": "declare_turn_goals", "classification": "internal", "result": {"ok": True}}],
        "task_board": [],
        "loop_plans": [],

    })
    install_test_semantic_contract(state, {
            "version": "turn-goal-plan@1.0",
            "turn": 1,
            "user_text": text,
            "goals": [{
                "goal_id": "most-expensive",
                "description": "查询当前集合中最贵的对象",
                "evidence_span": text,
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "expected_tools": [],
            }],
        })
    model = ScriptedChatModel([
        {"content": "我先分析一下应该查看哪个集合。"},
        {"tool_calls": [{
            "id": "select-most-expensive",
            "name": "list_orders",
            "args": {
                "goal_ids": ["most-expensive"],
                "target": {"mode": "all_orders"},
                "expected_shape": "collection",
                "reference_span": text,
            },
        }]},
    ])

    with model_call_scope(max_calls=2, scope="plain-prose-incomplete-workflow-regression"):
        first = agent_loop_node(
            state,
            context_bundle_builder=ContextBundleBuilder(transactions=Transactions()),
            capability_registry=get_runtime_registry().capabilities,
            model_resolver=lambda: model,
        )
        second = agent_loop_node(
            {**state, **first},
            context_bundle_builder=ContextBundleBuilder(transactions=Transactions()),
            capability_registry=get_runtime_registry().capabilities,
            model_resolver=lambda: model,
        )

    assert first["status"] == "WorkflowIncompleteRetry"
    assert int(first.get("answer_protocol_retry") or 0) == 0
    assert first["decision_chain"][-1]["decision"] == "plain_prose_rejected_for_incomplete_workflow"
    assert second["status"] == "AgentNextStepPlanned"
    assert second["phase"] == "loop_execute"
    assert "list_orders" in model.bound_tool_history[-1]
    assert "respond_to_user" not in model.bound_tool_history[-1]
    assert model.bound_tool_choices[-1] == "required"


def test_workflow_incomplete_retry_reopens_capabilities_instead_of_forcing_terminal():
    from langchain_core.messages import HumanMessage

    from agent_core.context import ContextBundleBuilder
    from agent_core.lifecycle.dialogue_runtime import agent_loop_node
    from agent_core.model_calls import model_call_scope
    from tests.support.scripted_chat_model import ScriptedChatModel

    class Transactions:
        def list_drafts_for_scope(self, **_kwargs):
            return []

    text = "订单10004能开发票吗？"
    state = _state(text)
    state.update({
        "state_schema_version": 2,
        "messages": [HumanMessage(content=text)],
        "status": "WorkflowIncompleteRetry",
        "agent_loop_step": 3,
        "agent_loop_max_steps": 6,
        "answer_protocol_retry": 1,
        "tool_trace": [{
            "classification": "observation",
            "name": "get_order_details",
            "result": _success("订单详情读取成功"),
        }],
        "task_board": [],
        "loop_plans": [],
        "frozen_semantic_contract": freeze_semantic_contract(
            turn=1,
            user_text=text,
            summary=text,
            goals=[{
                "goal_id": "invoice-consult",
                "description": "咨询订单10004发票政策",
                "evidence_span": text,
                "requested_effect": {
                    "domain": "invoice",
                    "operation": "consult_policy",
                    "object_type": "order",
                },
                "required": True,
                "depends_on": [],
            }],
            alignment_proof={"verdict": "exact", "source": "test"},
        ),
    })
    state["grounded_execution_plan"] = build_workflow_plan(
        state=state,
        turn_plan=state["current_turn_plan"],
        user_text=text,
    )
    model = ScriptedChatModel([{
        "tool_calls": [{
            "id": "invoice-policy",
            "name": "consult_invoice_policy",
            "args": {
                "goal_ids": ["invoice-consult"],
                "target": {"mode": "entity_match", "attribute_span": "订单10004"},
                "reference_span": "订单10004",
                "issue_span": "发票",
                "question_span": text,
            },
        }],
    }])

    with model_call_scope(max_calls=1, scope="workflow-incomplete-reopen-regression"):
        update = agent_loop_node(
            state,
            context_bundle_builder=ContextBundleBuilder(transactions=Transactions()),
            capability_registry=get_runtime_registry().capabilities,
            model_resolver=lambda: model,
        )

    assert update["status"] == "AgentNextStepPlanned"
    assert update["phase"] == "loop_execute"
    assert model.bound_tool_choices == ["required"]
    assert "consult_invoice_policy" in model.bound_tool_history[-1]
    assert "respond_to_user" not in model.bound_tool_history[-1]
    assert "get_order_details" not in model.bound_tool_history[-1]
    assert "ask_user_clarification" in model.bound_tool_history[-1]


def test_workflow_repair_rejects_provider_call_to_unexposed_terminal_tool():
    from langchain_core.messages import HumanMessage

    from agent_core.context import ContextBundleBuilder
    from agent_core.lifecycle.dialogue_runtime import agent_loop_node
    from agent_core.model_calls import model_call_scope
    from tests.support.scripted_chat_model import ScriptedChatModel

    class Transactions:
        def list_drafts_for_scope(self, **_kwargs):
            return []

    text = "订单10004能开发票吗？"
    state = _state(text)
    state.update({
        "messages": [HumanMessage(content=text)],
        "status": "WorkflowIncompleteRetry",
        "agent_loop_step": 3,
        "agent_loop_max_steps": 6,
        "answer_protocol_retry": 1,
        "tool_trace": [{
            "classification": "observation",
            "name": "get_order_details",
            "result": _success("订单详情读取成功"),
        }],

        "grounded_execution_plan": {
            "status": "RUNNING",
            "goals": [{
                "goal_id": "invoice-consult",
                "goal_type": "consult",
                "required": True,
                "coverage_status": "PENDING",
                "covered_by_step_ids": [],
                "covered_by_terminal_tools": [],
            }],
            "steps": [{"step_id": "step:1", "required": True, "status": "SUCCEEDED"}],
        },
    })
    install_test_semantic_contract(state, {
            "version": "turn-goal-plan@1.0",
            "turn": 1,
            "user_text": text,
            "goals": [{
                "goal_id": "invoice-consult",
                "description": "咨询订单10004发票政策",
                "evidence_span": text,
                "goal_type": "consult",
                "required": True,
                "depends_on": [],
                "expected_tools": [],
            }],
        })
    model = ScriptedChatModel([{
        # Simulate a provider repeating a historical function that is no
        # longer present in the dynamically narrowed repair tool surface.
        "tool_calls": [{
            "id": "stale-terminal",
            "name": "respond_to_user",
            "args": {
                "goal_ids": ["invoice-consult"],
                "answer": "可以开发票。",
                "evidence_handles": [],
            },
        }],
    }])

    with model_call_scope(max_calls=1, scope="workflow-unexposed-tool-regression"):
        update = agent_loop_node(
            state,
            context_bundle_builder=ContextBundleBuilder(transactions=Transactions()),
            capability_registry=get_runtime_registry().capabilities,
            model_resolver=lambda: model,
        )

    assert "respond_to_user" not in model.bound_tool_history[-1]
    assert update["status"] == "WorkflowIncompleteRetry"
    assert update["phase"] == "agent_loop"
    assert update["decision_chain"][-1]["decision"] == "model_called_unexposed_tool"
    assert update["decision_chain"][-1]["details"]["tools"] == ["respond_to_user"]
    assert any("TOOL_NOT_AVAILABLE_IN_CURRENT_WORKFLOW" in str(message.content) for message in update["messages"])


def test_invalid_goal_declaration_returns_authoritative_current_user_text():
    current = "其中最贵的是哪个？"
    result, plan = validate_goal_declaration(
        state=_state(current),
        args={
            "summary": "错误承接了另一轮文本",
            "goals": [{
                "goal_id": "g1",
                "description": "咨询蓝牙耳机退款",
                "evidence_span": "这个蓝牙耳机可以退货退款吗",
                "goal_type": "consult",
                "required": True,
                "depends_on": [],
            }],
        },
        capability_registry=get_runtime_registry().capabilities,
    )

    assert plan is None
    assert result["code"] == "GOAL_DECLARATION_INVALID"
    assert result["data"]["current_user_input"] == current
    assert result["data"]["repair_contract"] == {
        "authority": "current_user_input_only",
        "required_action": "redeclaration",
        "evidence_span_rule": "literal_contiguous_substring",
        "requested_effect_rule": "preserve the user's open business effect; do not coerce it into a nearby registered capability",
    }


def test_requested_effect_overrides_legacy_goal_type_without_program_reclassification():
    text = "先不问退款了，查订单10004能不能开发票"
    result, plan = validate_goal_declaration(
        state=_state(text),
        args={
            "summary": text,
            "goals": [{
                "goal_id": "invoice-policy",
                "description": "询问订单10004能否开发票",
                "evidence_span": "查订单10004能不能开发票",
                "requested_effect": {
                    "domain": "invoice",
                    "operation": "consult_policy",
                    "object_type": "order",
                },
                # Deliberately stale compatibility metadata. Runtime must not
                # use it to rewrite the formal requested effect.
                "goal_type": "query",
                "required": True,
                "depends_on": [],
            }],
        },
        capability_registry=get_runtime_registry().capabilities,
    )

    assert result["ok"] is True
    assert plan is not None
    formal = plan["_frozen_semantic_contract"]["goals"][0]
    assert formal["requested_effect"] == {
        "domain": "invoice",
        "operation": "consult_policy",
        "object_type": "order",
        "raw_description": "询问订单10004能否开发票",
    }


def test_goal_declaration_keeps_factual_invoice_existence_as_query():
    text = "查一下10004有没有发票"
    result, plan = validate_goal_declaration(
        state=_state(text),
        args={
            "summary": text,
            "goals": [{
                "goal_id": "invoice-record",
                "description": "查询订单发票记录",
                "evidence_span": "查一下10004有没有发票",
                "requested_effect": {"domain": "invoice", "operation": "query_record", "object_type": "order"},
                "goal_type": "query",
                "required": True,
                "depends_on": [],
            }],
        },
        capability_registry=get_runtime_registry().capabilities,
    )

    assert result["ok"] is True
    assert plan is not None
    assert plan["goals"][0]["goal_type"] == "query"


def test_elliptical_followup_gets_adaptive_independent_goal_verification(monkeypatch):
    text = "为什么？"
    state = _state(text)
    state["turn_index"] = 5
    monkeypatch.setenv("GOAL_ALIGNMENT_VERIFIER_MODE", "model")

    def verify(_self, *, user_text, goals, known_tools, recent_public_context, active_structured_interaction):
        assert user_text == text
        assert goals[0]["goal_type"] == "query"
        assert known_tools == set()
        assert recent_public_context == []
        assert active_structured_interaction is None
        return {
            "verdict": "incomplete",
            "evidence_spans": [],
            "missing_spans": ["为什么"],
            "reason_code": "explanation_must_be_narrative_not_fresh_query",
            "source": "model",
            "independent": True,
            "details": {"mode": "unified_semantic_review"},
        }

    monkeypatch.setattr(
        "agent_core.lifecycle.goal_planning.ModelGoalAlignmentVerifier.verify",
        verify,
    )
    result, plan = validate_goal_declaration(
        state=state,
        args={
            "summary": "解释上一轮退款资格结论",
            "goals": [{
                "goal_id": "explain",
                "description": "解释机械键盘具备退款资格的原因",
                "evidence_span": "为什么",
                "requested_effect": {"domain": "refund", "operation": "explain_assessment", "object_type": "order"},
                "goal_type": "query",
                "expected_result_cardinality": "single",
                "required": True,
                "depends_on": [],
            }],
        },
        capability_registry=get_runtime_registry().capabilities,
    )

    assert plan is None
    assert result["code"] == "GOAL_DECLARATION_INCOMPLETE"
    proof = result["data"]["alignment_proof"]
    assert proof["independent"] is True
    assert proof["details"]["mode"] == "unified_semantic_review"


def test_explicit_capability_followup_keeps_candidate_verifier_to_save_tokens(monkeypatch):
    text = "查退款记录"
    state = _state(text)
    state["turn_index"] = 5
    monkeypatch.setenv("GOAL_ALIGNMENT_VERIFIER_MODE", "candidate")
    monkeypatch.setattr(
        "agent_core.lifecycle.goal_planning.ModelGoalAlignmentVerifier.verify",
        lambda self, **kwargs: (_ for _ in ()).throw(
            AssertionError("direct literal capability match must not spend a goal verifier call")
        ),
    )

    result, plan = validate_goal_declaration(
        state=state,
        args={
            "summary": text,
            "goals": [{
                "goal_id": "refund-records",
                "description": "查询退款记录",
                "evidence_span": text,
                "requested_effect": {"domain": "refund", "operation": "list_records", "object_type": "refund"},
                "goal_type": "query",
                "expected_result_cardinality": "collection",
                "required": True,
                "depends_on": [],
            }],
        },
        capability_registry=get_runtime_registry().capabilities,
    )

    assert result["ok"] is True
    assert plan is not None
    assert plan["alignment_proof"]["source"] == "candidate_only"


def test_explicit_narrowing_followup_does_not_get_false_rejected_by_meta_verifier(monkeypatch):
    text = "只看机械键盘相关的。"
    state = _state(text)
    state["turn_index"] = 5
    monkeypatch.setenv("GOAL_ALIGNMENT_VERIFIER_MODE", "candidate")
    monkeypatch.setattr(
        "agent_core.lifecycle.goal_planning.ModelGoalAlignmentVerifier.verify",
        lambda self, **kwargs: (_ for _ in ()).throw(
            AssertionError("an explicit narrowed entity query is not a meta/elliptical turn")
        ),
    )

    result, plan = validate_goal_declaration(
        state=state,
        args={
            "summary": "只看机械键盘相关的售后工单",
            "goals": [{
                "goal_id": "keyboard-after-sales",
                "description": "查询机械键盘相关的售后工单",
                "evidence_span": "只看机械键盘相关的",
                "requested_effect": {"domain": "after_sales", "operation": "list", "object_type": "order"},
                "goal_type": "query",
                "expected_result_cardinality": "collection",
                "required": True,
                "depends_on": [],
            }],
        },
        capability_registry=get_runtime_registry().capabilities,
    )

    assert result["ok"] is True
    assert plan is not None
    assert plan["alignment_proof"]["source"] == "candidate_only"


def test_multi_observation_terminal_answer_does_not_collapse_to_last_outcome():
    from agent_core.lifecycle.dialogue_runtime import _terminal_runtime_outcome

    state = {
        "correlation_id": "multi-observation",
        "tool_trace": [
            {
                "classification": "observation",
                "effect_id": "effect:order-1",
                "result": {
                    "ok": True,
                    "runtime_outcome": {
                        "outcome_type": "query",
                        "evidence_handles": ["eligibility:1"],
                    },
                },
            },
            {
                "classification": "observation",
                "effect_id": "effect:order-2",
                "result": {
                    "ok": True,
                    "runtime_outcome": {
                        "outcome_type": "query",
                        "evidence_handles": ["eligibility:2"],
                    },
                },
            },
        ],
    }
    runtime = _terminal_runtime_outcome(
        state,
        call={"name": "respond_to_user"},
        answer="订单 1 可以退款；订单 2 不可以退款。",
        evidence_handles=["eligibility:2"],
    )

    assert runtime is not None
    assert runtime["outcome_type"] == "query"
    assert runtime["customer_safe_summary"] == "订单 1 可以退款；订单 2 不可以退款。"
    assert runtime["evidence_handles"] == ["eligibility:2", "eligibility:1"]
    assert runtime["payload"]["aggregation"]["observation_count"] == 2


def test_history_only_terminal_answer_gets_canonical_narrative_outcome():
    from agent_core.lifecycle.dialogue_runtime import _terminal_runtime_outcome

    runtime = _terminal_runtime_outcome(
        {
            "correlation_id": "history-recall",
            "runtime_outcome": None,
            "tool_trace": [
                {
                    "classification": "internal",
                    "name": "declare_turn_goals",
                    "result": {"ok": True},
                },
            ],
        },
        call={"name": "respond_to_user"},
        answer="刚才咨询的是订单 10004。",
        evidence_handles=["h_result:invoice-10004", "h_order:10004"],
    )

    assert runtime is not None
    assert runtime["outcome_type"] == "narrative"
    assert runtime["customer_safe_summary"] == "刚才咨询的是订单 10004。"
    assert runtime["evidence_handles"] == ["h_result:invoice-10004", "h_order:10004"]
    assert runtime["payload"]["aggregation"] == {
        "kind": "validated_terminal_answer",
        "observation_count": 0,
        "effect_ids": [],
    }


def test_single_current_observation_keeps_domain_owned_runtime_outcome():
    from agent_core.lifecycle.dialogue_runtime import _terminal_runtime_outcome
    from agent_core.runtime.outcomes import outcome

    domain_runtime = outcome(
        "query",
        customer_safe_summary="订单政策咨询结果。",
        evidence_handles=["h_result:invoice-10004"],
    ).as_dict()
    runtime = _terminal_runtime_outcome(
        {
            "runtime_outcome": domain_runtime,
            "tool_trace": [
                {
                    "classification": "observation",
                    "result": {"ok": True, "runtime_outcome": domain_runtime},
                },
            ],
        },
        call={"name": "respond_to_user"},
        answer="订单政策咨询结果。",
        evidence_handles=["h_result:invoice-10004"],
    )

    assert runtime is None


def test_failed_read_candidate_does_not_override_grounded_historical_terminal_answer():
    from agent_core.lifecycle.dialogue_runtime import _terminal_runtime_outcome

    runtime = _terminal_runtime_outcome(
        {
            "runtime_outcome": {
                "outcome_type": "failure",
                "effects": "none",
                "safe_to_continue": False,
                "customer_safe_summary": "当前请求无法安全完成。",
                "next_interaction": "none",
            },
            "tool_trace": [
                {
                    "classification": "observation",
                    "result": {
                        "ok": False,
                        "code": "CAPABILITY_UNAVAILABLE",
                        "runtime_outcome": {
                            "outcome_type": "failure",
                            "effects": "none",
                            "safe_to_continue": False,
                            "customer_safe_summary": "当前请求无法安全完成。",
                            "next_interaction": "none",
                        },
                    },
                },
            ],
        },
        call={"name": "respond_to_user"},
        answer="还在路上的唯一订单是 10001，因此它就是其中最贵的。",
        evidence_handles=["h_result:in-transit"],
    )

    assert runtime is not None
    assert runtime["outcome_type"] == "narrative"
    assert runtime["customer_safe_summary"] == "还在路上的唯一订单是 10001，因此它就是其中最贵的。"
    assert runtime["evidence_handles"] == ["h_result:in-transit"]


def test_canonical_observation_release_survives_model_terminal_protocol_failure():
    from agent_core.lifecycle.dialogue_runtime import _canonical_observation_release
    from agent_core.runtime.outcomes import outcome

    state = {
        "correlation_id": "consultation-release",
        "tool_trace": [
            {"classification": "internal", "name": "declare_turn_goals", "result": {"ok": True}},
            {
                "classification": "observation",
                "name": "consult_invoice_policy",
                "result": {
                    "ok": True,
                    "data": {"result_handle": "result:invoice-10004"},
                    "runtime_outcome": outcome(
                        "query",
                        customer_safe_summary="已完成已验证发票咨询。",
                        evidence_handles=[],
                        payload={"capability": "orders.issue_consultation"},
                    ).as_dict(),
                },
            },
        ],
    }

    released = _canonical_observation_release(state)

    assert released is not None
    runtime, summary, handles = released
    assert runtime["outcome_type"] == "query"
    assert summary == "已完成已验证发票咨询。"
    assert handles == ["result:invoice-10004"]
    assert runtime["evidence_handles"] == handles


def test_goal_declaration_rejects_non_literal_evidence_without_tool_guessing():
    state = _state("帮我查订单")
    registry = get_runtime_registry().capabilities
    result, plan = validate_goal_declaration(
        state=state,
        args={
            "summary": "错误计划",
            "goals": [{
                "goal_id": "g1",
                "description": "退款",
                "evidence_span": "用户没有说退款",
                "goal_type": "action",
                "required": True,
                "depends_on": [],
                "expected_tools": ["list_orders"],
            }],
        },
        capability_registry=registry,
    )
    assert result["ok"] is False
    assert plan is None
    errors = result["data"]["errors"]
    assert "evidence_not_in_current_turn:g1" in errors
    assert not any(error.startswith("action_goal_requires_action_draft:") for error in errors)


def test_independent_goal_alignment_verifier_rejects_omitted_user_branch():
    class IncompleteVerifier:
        def verify(self, *, user_text, goals, known_tools):
            assert user_text == "查一下订单，再查物流"
            assert {goal["goal_id"] for goal in goals} == {"g1"}
            assert known_tools == set()
            return {
                "verdict": "incomplete",
                "evidence_spans": ["查一下订单"],
                "missing_spans": ["查物流"],
                "reason_code": "logistics_goal_missing",
                "source": "test_independent",
                "independent": True,
            }

    state = _state("查一下订单，再查物流")
    state["goal_alignment_verifier"] = IncompleteVerifier()
    result, plan = validate_goal_declaration(
        state=state,
        args={
            "summary": "只声明了订单查询",
            "goals": [{
                "goal_id": "g1",
                "description": "查订单",
                "evidence_span": "查一下订单",
                "requested_effect": {"domain": "order", "operation": "list", "object_type": "order"},
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "expected_tools": ["list_orders"],
            }],
        },
        capability_registry=get_runtime_registry().capabilities,
    )
    assert result["ok"] is False
    assert result["code"] == "GOAL_DECLARATION_INCOMPLETE"
    assert result["data"]["alignment_proof"]["missing_spans"] == ["查物流"]
    assert plan is None


def test_goal_plan_persists_independent_alignment_proof_when_exact():
    class ExactVerifier:
        def verify(self, *, user_text, goals, known_tools):
            return {
                "verdict": "exact",
                "evidence_spans": ["查一下订单", "查物流"],
                "missing_spans": [],
                "reason_code": "all_requested_outcomes_declared",
                "source": "test_independent",
                "independent": True,
            }

    state = _state("查一下订单，再查物流")
    state["goal_alignment_verifier"] = ExactVerifier()
    result, plan = validate_goal_declaration(
        state=state,
        args={
            "summary": "订单与物流两个目标",
            "goals": [
                {
                    "goal_id": "g1",
                    "description": "查订单",
                    "evidence_span": "查一下订单",
                    "requested_effect": {"domain": "order", "operation": "list", "object_type": "order"},
                    "goal_type": "query",
                    "required": True,
                    "depends_on": [],
                    "expected_tools": ["list_orders"],
                },
                {
                    "goal_id": "g2",
                    "description": "查物流",
                    "evidence_span": "查物流",
                    "requested_effect": {"domain": "logistics", "operation": "query", "object_type": "order"},
                    "goal_type": "query",
                    "required": True,
                    "depends_on": ["g1"],
                    "expected_tools": ["get_order_logistics"],
                },
            ],
        },
        capability_registry=get_runtime_registry().capabilities,
    )
    assert result["ok"] is True
    assert plan is not None
    assert plan["alignment_proof"]["verdict"] == "exact"
    assert plan["alignment_proof"]["independent"] is True
