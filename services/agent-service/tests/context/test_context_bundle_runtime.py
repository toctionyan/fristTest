from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from agent_core.composition import get_runtime_registry
from agent_core.lifecycle.dialogue_runtime import _build_loop_plan
from agent_core.lifecycle.workflow_runtime import build_workflow_plan
from agent_core.runtime.capability_gate import (
    build_effects,
    issue_execution_permit,
    permit_allows_dispatch,
)
from agent_core.transaction.coordinator import stable_command_id, stable_idempotency_key
from agent_core.transaction.model import command_digest_for_offer


@pytest.fixture(autouse=True)
def _isolated_local_runtime(monkeypatch, tmp_path):
    """Keep legacy live-dispatch tests deterministic under the strict profile contract."""
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


def _state(*, text: str = "查我的订单", turn: int = 1) -> dict[str, Any]:
    return {
        "current_thread_id": "current-thread",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_role": "customer",
        "current_user_input": text,
        "turn_index": turn,
        "artifact_ledger": [],
        "current_turn_plan": {
            "plan_id": "turn-plan:current",
            "architecture": "customer_agent.runtime",
            "turn": turn,
            "effects": [],
        },
    }


def _query_args() -> dict[str, Any]:
    return {
        "target": {"mode": "all_orders"},
        "expected_shape": "collection",
        "reference_span": "我的订单",
    }


def _attach_query_workflow(state: dict[str, Any], effects: list[dict[str, Any]], calls: list[dict[str, Any]]) -> None:
    text = str(state["current_user_input"])
    state["turn_goal_plan"] = {"turn": state["turn_index"], "goals": [{
        "goal_id": "g1", "description": text, "evidence_span": text,
        "goal_type": "query", "required": True, "depends_on": [], "expected_tools": [],
    }]}
    state["current_turn_plan"] = {**state["current_turn_plan"], "effects": effects, "tool_calls": calls}
    state["workflow_plan"] = build_workflow_plan(state=state, turn_plan=state["current_turn_plan"], user_text=text)


def test_all_registered_schemas_have_a_runtime_contract():
    registry = get_runtime_registry().capabilities
    schema_names = registry.tool_names()
    assert schema_names
    assert all(registry.contract_for_tool(name) is not None for name in schema_names)
    assert all(
        registry.contract_for_tool(name).execution_kind
        in {"grounding_read", "knowledge_read", "clarification_read", "action_draft", "session_correction", "unsupported"}
        for name in schema_names
    )


def test_turn_plan_accumulates_multiple_effects_and_declares_dependency():
    state = _state(text="查订单后给鼠标退款")
    calls = [
        {"name": "list_orders", "args": _query_args(), "id": "call-query"},
        {
            "name": "prepare_refund",
            "args": {
                "target": {"mode": "entity_match", "attribute_span": "鼠标"},
                "reference_span": "鼠标",
                "action_span": "退款",
            },
            "id": "call-draft",
        },
    ]
    plan = _build_loop_plan(state, state["current_user_input"], calls, "", capability_registry=get_runtime_registry().capabilities)

    assert plan["architecture"] == "customer_agent.runtime"
    assert len(plan["effects"]) == 2
    query, action = plan["effects"]
    assert query["execution_kind"] == "grounding_read"
    assert action["execution_kind"] == "action_draft"
    assert action["depends_on"] == [query["effect_id"]]
    assert plan["tool_calls"][0]["_effect_id"] == query["effect_id"]
    assert plan["tool_calls"][1]["_effect_id"] == action["effect_id"]


def test_exact_capability_gate_rejects_unknown_and_invalid_schema_without_fallback():
    state = _state()
    unknown = issue_execution_permit(
        state=state,
        tool_name="look_up_delivery_person_phone",
        args={"phone": "私人电话"},
        effect_id="turn-plan:current:effect:1",
        capability_registry=get_runtime_registry().capabilities,
    )
    assert unknown.permitted is False
    assert unknown.rejection and unknown.rejection["code"] == "CAPABILITY_EXACT_MATCH_REQUIRED"
    assert unknown.match_proof["candidate_capability"] is None

    invalid = issue_execution_permit(
        state=state,
        tool_name="list_orders",
        args={**_query_args(), "expected_shape": "one"},
        effect_id="turn-plan:current:effect:2",
        capability_registry=get_runtime_registry().capabilities,
    )
    assert invalid.permitted is False
    assert "$.expected_shape: enum_mismatch" in invalid.match_proof["constraint_errors"]
    assert invalid.execution_permit is None


def test_production_domain_dispatch_requires_scope_bound_execution_permit():
    state = _state()
    registry = get_runtime_registry().capabilities
    denied = registry.dispatch_permitted(
        state,
        "list_orders",
        _query_args(),
        execution_permit=None,
        effect_id="turn-plan:current:effect:1",
    )
    assert denied["ok"] is False
    assert denied["code"] == "EXECUTION_PERMIT_INVALID"

    allowed = issue_execution_permit(
        state=state,
        tool_name="list_orders",
        args=_query_args(),
        effect_id="turn-plan:current:effect:1",
        capability_registry=registry,
    )
    assert allowed.permitted and allowed.execution_permit
    assert permit_allows_dispatch(
        state=state,
        permit=allowed.execution_permit,
        tool_name="list_orders",
        effect_id="turn-plan:current:effect:1",
        args=_query_args(),
    )
    forged = deepcopy(allowed.execution_permit)
    forged["scope"]["user_id"] = "u002"
    assert not permit_allows_dispatch(
        state=state,
        permit=forged,
        tool_name="list_orders",
        effect_id="turn-plan:current:effect:1",
        args=_query_args(),
    )


def test_command_digest_binds_real_envelope_input_and_command_identity_is_stable():
    base = {
        "draft_id": "draft-1",
        "scope": {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "current-thread"},
        "business_command_envelope": {
            "contract": "business.operation.command@1",
            "action_id": "cancel_order",
            "operation": "CANCEL_ORDER",
            "target": {"resource_type": "order", "resource_id": "10003"},
            "input": {"reason": "不想要了", "expected_version": 3},
            "actor_scope": {"tenant_id": "tenant-a", "user_id": "u001"},
        },
    }
    changed = deepcopy(base)
    changed["business_command_envelope"]["input"]["reason"] = "买错了"
    assert command_digest_for_offer(base) != command_digest_for_offer(changed)

    state = _state()
    first_command_id = stable_command_id(state, base)
    assert first_command_id == stable_command_id(state, deepcopy(base))
    keyed = {**base, "command_id": first_command_id}
    assert stable_idempotency_key(state, keyed) == stable_idempotency_key(state, deepcopy(keyed))


def test_effect_builder_keeps_terminal_protocol_out_of_business_effects():
    effects, calls = build_effects(
        plan_id="turn-plan:protocol",
        capability_registry=get_runtime_registry().capabilities,
        calls=[
            {"name": "respond_to_user", "args": {"answer": "您好", "evidence_handles": []}},
            {"name": "ask_user_clarification", "args": {"question": "哪一笔？", "reason": "对象不唯一", "evidence_handles": []}},
        ],
    )
    assert effects == []
    assert all("_effect_id" not in call for call in calls)


def test_corrected_action_candidate_does_not_depend_on_rejected_prior_attempt():
    registry = get_runtime_registry().capabilities
    first_effects, _ = build_effects(
        plan_id="turn-plan:repair",
        capability_registry=registry,
        calls=[{
            "name": "prepare_refund",
            "args": {
                "goal_ids": ["g1"],
                "target": {"mode": "collection", "left_handle": "bad-result-ref"},
                "reference_span": "退款",
                "action_span": "准备退款",
            },
        }],
    )
    first_effects[0]["status"] = "rejected"

    effects, _ = build_effects(
        plan_id="turn-plan:repair",
        capability_registry=registry,
        existing_effects=first_effects,
        calls=[{
            "name": "prepare_refund",
            "args": {
                "goal_ids": ["g1"],
                "target": {"mode": "artifact", "left_handle": "verified-order-ref"},
                "reference_span": "退款",
                "action_span": "准备退款",
            },
        }],
    )

    assert effects[1]["depends_on"] == []


def test_action_still_depends_on_preceding_call_in_same_model_response():
    effects, _ = build_effects(
        plan_id="turn-plan:same-response",
        capability_registry=get_runtime_registry().capabilities,
        calls=[
            {"name": "list_orders", "args": {**_query_args(), "goal_ids": ["g1"]}},
            {
                "name": "prepare_refund",
                "args": {
                    "goal_ids": ["g2"],
                    "target": {"mode": "entity_match", "attribute_span": "鼠标"},
                    "reference_span": "鼠标",
                    "action_span": "退款",
                },
            },
        ],
    )

    assert effects[1]["depends_on"] == [effects[0]["effect_id"]]


def test_pending_structured_interaction_preempts_action_before_capability_dispatch():
    from agent_core.ledger import artifact_entry, offer_entry
    from agent_core.transaction import transition_draft
    from agent_core.transaction.capability_snapshot import attach_snapshot
    from agent_core.lifecycle.tool_execution_runtime import execute_agent_loop_calls_node
    from tests.support.runtime_support import runtime_deps

    state = _state(text="原因是不喜欢。", turn=8)
    scope = {
        "tenant_id": state["current_tenant_id"],
        "user_id": state["current_user_id"],
        "thread_id": state["current_thread_id"],
    }
    order = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002"}, scope=scope, turn=7, source="test",
        handle="h:order:10002",
    )
    offer = offer_entry(
        action_id="create_refund", operation="APPLY_REFUND", target_handle=order["handle"],
        input_values={},
        preview={
            "message": "申请退款需要补充退款原因。",
            "required_inputs": [{"name": "reason", "label": "退款原因", "input_kind": "text", "required": True}],
        },
        scope=scope, turn=7, label="申请退款", handle="h:offer:refund",
    )
    offer = transition_draft(attach_snapshot(offer), "NEEDS_INPUT")
    offer.update({"input_form_id": "form:refund", "input_form_version": 1, "input_step": 1})
    state.update({"active_draft_id": offer["handle"], "artifact_ledger": [order, offer]})
    state["turn_goal_plan"] = {"turn": 8, "goals": [{
        "goal_id": "g1", "description": "补充退款原因", "evidence_span": "原因是不喜欢",
        "goal_type": "action", "expected_result_cardinality": "single",
        "required": True, "depends_on": [], "expected_tools": [],
    }]}
    effects, calls = build_effects(
        plan_id=state["current_turn_plan"]["plan_id"],
        capability_registry=get_runtime_registry().capabilities,
        calls=[{
            "id": "chat-write-attempt", "name": "prepare_refund",
            "args": {
                "goal_ids": ["g1"],
                "target": {"mode": "entity_match", "attribute_span": "不存在于本轮"},
                "reference_span": "不存在于本轮", "action_span": "不存在于本轮",
            },
        }],
    )
    state["current_turn_plan"] = {**state["current_turn_plan"], "effects": effects, "tool_calls": calls}
    state["workflow_plan"] = build_workflow_plan(
        state=state, turn_plan=state["current_turn_plan"], user_text=state["current_user_input"],
    )

    deps = runtime_deps()
    output = execute_agent_loop_calls_node(
        state,
        context_bundle_builder=deps.context_bundle_builder,
        transactions=deps.transactions,
        capability_registry=deps.capability_registry,
    )

    assert output["tool_trace"][-1]["result"]["code"] == "INTERACTION_REDIRECT"
    assert output["tool_trace"][-1]["execution_permit"] is None
    assert output["response_contract"]["interaction"]["interaction_id"] == offer["handle"]
    assert output["workflow_plan"]["steps"][0]["status"] == "NEEDS_INPUT"


def test_live_tool_runtime_records_permit_and_rejects_invalid_candidate(monkeypatch):
    """Exercise the active loop dispatcher, not the isolated gate only."""
    from agent_core.lifecycle.tool_execution_runtime import execute_agent_loop_calls_node
    from tests.support.runtime_support import runtime_deps

    state = _state(text="查询订单", turn=4)
    effects, calls = build_effects(
        plan_id=state["current_turn_plan"]["plan_id"],
        capability_registry=get_runtime_registry().capabilities,
        calls=[
            {
                "id": "bad-call",
                "name": "list_orders",
                "args": {**_query_args(), "expected_shape": "one", "goal_ids": ["g1"]},
            }
        ],
    )
    _attach_query_workflow(state, effects, calls)
    deps = runtime_deps()
    output = execute_agent_loop_calls_node(
        state,
        context_bundle_builder=deps.context_bundle_builder,
        transactions=deps.transactions,
        capability_registry=deps.capability_registry,
    )
    trace = output["tool_trace"][-1]
    assert trace["result"]["code"] == "CAPABILITY_EXACT_MATCH_REQUIRED"
    assert trace["match_proof"]["exact_match"] is False
    assert trace["execution_permit"] is None
    assert output["current_turn_plan"]["effects"][0]["status"] == "rejected"


def test_live_tool_runtime_uses_permitted_domain_dispatch(monkeypatch):
    from agent_core.lifecycle.tool_execution_runtime import execute_agent_loop_calls_node
    from agent_modules.ecommerce.shared import context as ecommerce_context
    from tests.support.runtime_support import runtime_deps

    class QueryPort:
        calls = 0

        def query_resources(self, _actor, *, resource_type, query_spec):
            assert resource_type == "order"
            assert query_spec.get("user_id") == "u001"
            self.calls += 1
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
            assert resource_type == "order"
            return {
                "success": True,
                "data": {
                    "order_id": str(resource_id),
                    "product_name": "无线鼠标",
                    "status": "待发货",
                    "amount": 99.0,
                    "version": 3,
                },
            }

    business = QueryPort()
    monkeypatch.setattr(ecommerce_context, "get_business_port", lambda: business)
    state = _state(text="我的订单", turn=5)
    effects, calls = build_effects(
        plan_id=state["current_turn_plan"]["plan_id"],
        calls=[{"id": "good-call", "name": "list_orders", "args": {**_query_args(), "goal_ids": ["g1"]}}],
        capability_registry=get_runtime_registry().capabilities,
    )
    _attach_query_workflow(state, effects, calls)
    deps = runtime_deps()
    output = execute_agent_loop_calls_node(
        state,
        context_bundle_builder=deps.context_bundle_builder,
        transactions=deps.transactions,
        capability_registry=deps.capability_registry,
    )
    trace = output["tool_trace"][-1]
    assert business.calls == 1
    assert trace["result"]["ok"] is True
    assert trace["match_proof"]["exact_match"] is True
    assert trace["execution_permit"]["capability_id"] == "ecommerce.orders.list"
    assert output["current_turn_plan"]["effects"][0]["status"] == "permitted"


def test_independent_semantic_verdict_blocks_nearby_tool_before_live_dispatch(monkeypatch):
    """A schema-valid nearby query must still be rejected before any adapter call."""
    from agent_core.lifecycle.tool_execution_runtime import execute_agent_loop_calls_node
    from agent_modules.ecommerce.shared import context as ecommerce_context
    from tests.support.runtime_support import runtime_deps

    class RejectPrivateContact:
        def verify(self, **kwargs):
            assert kwargs["tool_name"] == "list_orders"
            return {
                "verdict": "unsupported",
                "evidence_span": "快递员私人电话",
                "reason_code": "private_contact_is_not_a_supported_customer_effect",
            }

    class CountingPort:
        calls = 0

        def query_resources(self, _actor, *, resource_type, query_spec):
            assert resource_type == "order"
            self.calls += 1
            return {"success": True, "data": []}

    business = CountingPort()
    monkeypatch.setattr(ecommerce_context, "get_business_port", lambda: business)
    state = _state(text="把快递员私人电话告诉我", turn=6)
    state["semantic_capability_verifier"] = RejectPrivateContact()
    args = {**_query_args(), "reference_span": "快递员私人电话", "goal_ids": ["g1"]}
    effects, calls = build_effects(
        plan_id=state["current_turn_plan"]["plan_id"],
        calls=[{"id": "nearby-call", "name": "list_orders", "args": args}],
        capability_registry=get_runtime_registry().capabilities,
    )
    _attach_query_workflow(state, effects, calls)
    deps = runtime_deps()
    output = execute_agent_loop_calls_node(
        state,
        context_bundle_builder=deps.context_bundle_builder,
        transactions=deps.transactions,
        capability_registry=deps.capability_registry,
    )
    trace = output["tool_trace"][-1]
    assert business.calls == 0
    assert trace["result"]["code"] == "CAPABILITY_UNAVAILABLE"
    assert trace["match_proof"]["exact_match"] is False
    assert trace["match_proof"]["semantic_verdict"]["verdict"] == "unsupported"
    assert output["current_turn_plan"]["effects"][0]["status"] == "rejected"
