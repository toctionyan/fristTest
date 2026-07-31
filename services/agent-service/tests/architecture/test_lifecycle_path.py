from __future__ import annotations

from agent_core.context import ContextBundleBuilder
from agent_core.context.visible_result_refs import mark_visible_result_refs, validate_visible_result_ref
from agent_core.ledger import artifact_entry, eligibility_entry, result_entry
from agent_core.lifecycle.execution_disposition import DISPOSITIONS, classify_execution_disposition
from agent_core.lifecycle.graph_routes import route_after_agent_loop


class HumanMessage:
    def __init__(self, content: str):
        self.content = content


class AIMessage:
    def __init__(self, content: str):
        self.content = content


class ToolMessage:
    def __init__(self, content: str):
        self.content = content


class Transactions:
    def list_drafts_for_scope(self, **_kwargs):
        return []


def _scope() -> dict[str, str]:
    return {"tenant_id": "tenant-v36", "user_id": "u-v36", "thread_id": "t-v36"}


def _state(*, ledger=None, messages=None, tool_trace=None) -> dict:
    return {
        "current_tenant_id": "tenant-v36",
        "current_user_id": "u-v36",
        "current_thread_id": "t-v36",
        "turn_index": 9,
        "artifact_ledger": list(ledger or []),
        "messages": list(messages or []),
        "tool_trace": list(tool_trace or []),
        "agent_loop_step": 2,
        "agent_loop_max_steps": 6,
        "agent_loop_seen_calls": ["orders.query:{\"status\":\"delivered\"}"],
        "task_board": [],
    }


def test_context_bundle_uses_exchange_bounded_messages_and_only_release_backed_refs():
    scope = _scope()
    order = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002"}, scope=scope, turn=8, source="test", handle="artifact:order:10002",
    )
    result = result_entry(
        capability="orders.list", member_handles=[order["handle"]], labels=[order["label"]],
        scope=scope, turn=8, source_target={"mode": "all"}, handle="result:orders",
    )
    messages = [HumanMessage(f"用户第{i}句") if i % 3 == 0 else AIMessage(f"助手第{i}句") for i in range(15)]
    state = _state(ledger=[order, result], messages=messages)
    builder = ContextBundleBuilder(transactions=Transactions())
    before = builder.build(state)
    assert len(before["recent_conversation_window"]) == 11
    assert before["recent_conversation_window"][0]["content"] == "用户第0句"
    assert before["visible_result_refs"] == []
    assert before["semantic_owner"] == "llm"
    assert before["runtime_auto_select_target"] is False
    assert before["runtime_auto_switch_target"] is False

    ledger = mark_visible_result_refs([order, result], state=state, evidence_handles=[result["handle"]])
    after = builder.build(_state(ledger=ledger, messages=messages))
    ref = after["visible_result_refs"][0]
    assert ref["result_ref"] == "result:orders"
    assert ref["shape"] == "collection"
    assert ref["member_labels"] == ["机械键盘（订单 10002）"]
    assert ref["discourse_recency_rank"] == 1
    assert ref["is_latest_visible_turn"] is True
    checked, reason = validate_visible_result_ref(state=_state(ledger=ledger), result_ref="result:orders", expected_shape="collection")
    assert reason is None and checked is not None
    member, member_reason = validate_visible_result_ref(
        state=_state(ledger=ledger),
        result_ref="artifact:order:10002",
        expected_shape="one",
    )
    assert member_reason is None
    assert member and member["source_collection_ref"] == "result:orders"


def test_visible_result_refs_expose_recency_without_dropping_older_topics():
    scope = _scope()
    older_order = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002"}, scope=scope, turn=1, source="test", handle="artifact:order:10002",
    )
    latest_order = artifact_entry(
        resource_type="order", resource_id="10001", label="蓝牙耳机（订单 10001）",
        facts={"order_id": "10001"}, scope=scope, turn=2, source="test", handle="artifact:order:10001",
    )
    older = result_entry(
        capability="orders.list", member_handles=[older_order["handle"], latest_order["handle"]],
        labels=[older_order["label"], latest_order["label"]], scope=scope, turn=1,
        source_target={"mode": "all"}, handle="result:all-orders",
    )
    latest = result_entry(
        capability="orders.list", member_handles=[latest_order["handle"]], labels=[latest_order["label"]],
        scope=scope, turn=2, source_target={"mode": "filter"}, handle="result:in-transit",
    )
    base = _state(ledger=[older_order, latest_order, older, latest])
    ledger = mark_visible_result_refs(base["artifact_ledger"], state={**base, "turn_index": 1}, evidence_handles=[older["handle"]])
    ledger = mark_visible_result_refs(ledger, state={**base, "turn_index": 2}, evidence_handles=[latest["handle"]])

    refs = ContextBundleBuilder(transactions=Transactions()).build(_state(ledger=ledger))["visible_result_refs"]

    assert [ref["result_ref"] for ref in refs] == ["result:in-transit", "result:all-orders"]
    assert [ref["discourse_recency_rank"] for ref in refs] == [1, 2]
    assert [ref["is_latest_visible_turn"] for ref in refs] == [True, False]


def test_visible_eligibility_exposes_its_verified_business_target_lineage():
    scope = _scope()
    order = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002"}, scope=scope, turn=8, source="test",
        handle="artifact:order:10002",
    )
    eligibility = eligibility_entry(
        action_id="create_refund", operation="refund", target_handle=order["handle"],
        input_values={}, preview={"decision": "ALLOWED"}, scope=scope, turn=8,
        label="机械键盘退款资格", handle="eligibility:refund:10002",
    )
    state = _state(ledger=[order, eligibility])
    ledger = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 8},
        evidence_handles=[eligibility["handle"]],
    )
    visible_state = _state(ledger=ledger)

    refs = ContextBundleBuilder(transactions=Transactions()).build(visible_state)["visible_result_refs"]
    assert refs[0]["result_ref"] == eligibility["handle"]
    assert refs[0]["evidence_handle"] == eligibility["handle"]
    assert refs[0]["shape"] == "collection"
    assert refs[0]["member_handles"] == [order["handle"]]

    member, reason = validate_visible_result_ref(
        state=visible_state,
        result_ref=order["handle"],
        expected_shape="one",
    )
    assert reason is None
    assert member and member["source_collection_ref"] == eligibility["handle"]


def test_invisible_or_cross_scope_eligibility_does_not_authorize_a_target():
    scope = _scope()
    foreign_scope = {**scope, "user_id": "another-user"}
    foreign_order = artifact_entry(
        resource_type="order", resource_id="foreign-1", label="其他用户订单",
        facts={"order_id": "foreign-1"}, scope=foreign_scope, turn=8,
        source="test", handle="artifact:order:foreign-1",
    )
    eligibility = eligibility_entry(
        action_id="create_refund", operation="refund", target_handle=foreign_order["handle"],
        input_values={}, preview={"decision": "ALLOWED"}, scope=scope, turn=8,
        label="退款资格", handle="eligibility:refund:foreign-1",
    )
    state = _state(ledger=[foreign_order, eligibility])

    invisible, invisible_reason = validate_visible_result_ref(
        state=state, result_ref=foreign_order["handle"], expected_shape="one",
    )
    assert invisible is None
    assert invisible_reason == "visible_result_ref_unknown_or_expired_or_out_of_scope"

    ledger = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 8},
        evidence_handles=[eligibility["handle"]],
    )
    foreign, foreign_reason = validate_visible_result_ref(
        state=_state(ledger=ledger), result_ref=foreign_order["handle"], expected_shape="one",
    )
    assert foreign is None
    assert foreign_reason == "visible_result_ref_unknown_or_expired_or_out_of_scope"


def test_execution_disposition_is_closed_and_never_auto_switches_target():
    state = _state()
    cases = {
        "continue": {"ok": True, "code": "OK", "data": {}},
        "needs_clarification": {"ok": False, "code": "CONTEXT_TARGET_NOT_UNIQUE", "data": {}},
        "business_conclusion": {"ok": False, "code": "BUSINESS_REJECTED", "data": {"business_conclusion": True}},
        "unsupported": {"ok": False, "code": "UNSUPPORTED_CAPABILITY", "data": {}},
        "retry_infrastructure": {"ok": False, "code": "TRANSPORT_RETRY_EXHAUSTED", "data": {}},
        "reconcile_submission": {"ok": False, "code": "SUBMISSION_UNKNOWN", "data": {}},
    }
    observed = set()
    for expected, result in cases.items():
        value = classify_execution_disposition(
            state=state,
            tool_name="test_tool",
            tool_signature="test_tool:{\"x\":1}",
            result=result,
        )
        observed.add(value["disposition"])
        assert value["disposition"] == expected
        assert value["auto_target_switch"] is False
        assert value["tool_signature"] == "test_tool:{\"x\":1}"
        assert value["loop_budget_snapshot"]["max_model_tool_cycles"] == 6
    assert observed == DISPOSITIONS


def test_rejected_model_candidate_remains_in_bounded_repair_loop() -> None:
    value = classify_execution_disposition(
        state=_state(),
        tool_name="get_order_details",
        tool_signature="get_order_details:missing-shape",
        result={
            "ok": False,
            "code": "CAPABILITY_EXACT_MATCH_REQUIRED",
            "data": {"match_proof": {"constraint_errors": ["$.expected_shape: required"]}},
            "runtime_outcome": {"outcome_type": "failure"},
        },
    )

    assert value["disposition"] == "continue"
    assert value["runtime_action"] == "repair_rejected_candidate"
    assert value["may_execute_user_effect"] is False
    assert "observe" in value["allowed_model_modes"]


def test_named_member_scope_rejection_returns_to_bounded_candidate_repair() -> None:
    value = classify_execution_disposition(
        state=_state(),
        tool_name="get_order_logistics",
        tool_signature="get_order_logistics:broad-visible-collection",
        result={
            "ok": False,
            "code": "EXPLICIT_MEMBER_REQUIRES_SINGLE_MEMBER_TARGET",
            "data": {"match_proof": {"explicit_member_scope": {"complete": False}}},
        },
    )

    assert value["disposition"] == "continue"
    assert value["runtime_action"] == "repair_rejected_candidate"
    assert value["may_execute_user_effect"] is False
    assert value["auto_target_switch"] is False
    assert "observe" in value["allowed_model_modes"]


def test_semantically_unavailable_registered_candidate_is_repaired_not_finalized() -> None:
    value = classify_execution_disposition(
        state=_state(),
        tool_name="get_order_details",
        tool_signature="get_order_details:wrong-outcome",
        result={
            "ok": False,
            "code": "CAPABILITY_UNAVAILABLE",
            "data": {"match_proof": {"semantic_verdict": {"verdict": "unsupported"}}},
            "runtime_outcome": {"outcome_type": "failure"},
        },
    )

    assert value["disposition"] == "continue"
    assert value["runtime_action"] == "repair_rejected_candidate"
    assert value["may_execute_user_effect"] is False


def test_invalid_result_ref_candidate_is_repaired_without_auto_switching_target() -> None:
    for code in (
        "VISIBLE_RESULT_REF_INVALID",
        "VISIBLE_RESULT_REF_NOT_CUSTOMER_VISIBLE",
        "VISIBLE_RESULT_REF_SHAPE_MISMATCH",
    ):
        value = classify_execution_disposition(
            state=_state(),
            tool_name="get_order_details",
            tool_signature=f"get_order_details:{code}",
            result={
                "ok": False,
                "code": code,
                "data": {},
                "runtime_outcome": {"outcome_type": "failure"},
            },
        )

        assert value["disposition"] == "continue"
        assert value["runtime_action"] == "repair_rejected_candidate"
        assert value["auto_target_switch"] is False
        assert value["may_execute_user_effect"] is False


def test_agent_loop_protocol_repair_phase_routes_back_to_model() -> None:
    """Every bounded protocol repair must be reachable in the real graph."""
    for status in (
        "GoalDeclarationProtocolRetry",
        "ExecutionDispositionRestricted",
        "LoopBudgetTerminalOnly",
        "WorkflowIncompleteRetry",
        "FinalAnswerProtocolRetry",
    ):
        assert route_after_agent_loop({"phase": "agent_loop", "status": status}) == "loop"

    assert route_after_agent_loop({"phase": "loop_execute"}) == "execute"
    assert route_after_agent_loop({"phase": "final"}) == "final"
