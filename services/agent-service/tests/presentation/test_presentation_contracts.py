from __future__ import annotations

import json
from copy import deepcopy

from app.services.response_projector import ResponseProjector
from agent_core.presentation.registry import PresentationRegistry, build_response_blocks
from agent_core.composition import get_runtime_registry
from agent_core.runtime.capability_gate import issue_execution_permit
from agent_core.runtime.semantic_capability_verifier import CandidateOnlySemanticVerifier
from agent_modules.ecommerce.presentation.adapter import EcommerceObservationAdapter
from agent_modules.ecommerce.presentation.contracts import ORDER_LIST_CONTRACT
from agent_modules.ecommerce import _shared_execution as ecommerce_execution
from agent_modules.ecommerce.shared import context as ecommerce_context


class _Business:
    def __init__(self) -> None:
        self.rows = [
            {
                "order_id": "10004",
                "product_name": "定制马克杯",
                "status": "已签收",
                "amount": 59.0,
                "version": 2,
            },
            {
                "order_id": "10003",
                "product_name": "无线鼠标",
                "status": "待发货",
                "amount": 99.0,
                "version": 3,
            },
        ]

    def query_resources(self, _actor, *, resource_type, query_spec):
        assert resource_type == "order"
        assert query_spec.get("user_id") == "u001"
        return {"success": True, "data": deepcopy(self.rows)}

    def read_resource(self, _actor, *, resource_type, resource_id, query=None):
        assert resource_type == "order"
        assert (query or {}).get("user_id") == "u001"
        row = next(item for item in self.rows if item["order_id"] == resource_id)
        return {"success": True, "data": deepcopy(row)}

    def query_related_resources(self, _actor, *, resource_type, relation, query_spec):
        assert resource_type == "after_sales"
        assert relation.get("user_id") == "u001"
        assert query_spec == {"status": None}
        by_order = {
            "10004": [{
                "ticket_id": "AS-10004",
                "order_id": "10004",
                "status": "处理中",
                "updated_at": "2026-07-15T10:00:00Z",
                "version": 2,
            }],
            "10003": [],
        }
        return {
            "success": True,
            "data": deepcopy(by_order.get(str(relation.get("order_id") or ""), [])),
        }


def _state() -> dict:
    return {
        "current_thread_id": "presentation-thread",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_role": "customer",
        "current_user_input": "我买了什么",
        "turn_index": 7,
        "artifact_ledger": [],
    }


def _query_args() -> dict:
    return {
        "target": {"mode": "all_orders"},
        "expected_shape": "collection",
        "reference_span": "我买了什么",
    }


def _dispatch(state: dict, tool_name: str, args: dict) -> dict:
    # Presentation tests exercise the projection boundary, not a live model.
    # Inject the explicit local/test verifier so an API key in .env cannot
    # silently turn these unit tests into network-dependent integration tests.
    state.setdefault("semantic_capability_verifier", CandidateOnlySemanticVerifier())
    registry = get_runtime_registry().capabilities
    effect_id = f"v182:{tool_name}:{state.get('turn_index', 0)}"
    decision = issue_execution_permit(
        state=state,
        tool_name=tool_name,
        args=args,
        effect_id=effect_id,
        capability_registry=registry,
    )
    assert decision.permitted and decision.execution_permit
    return registry.dispatch_permitted(
        state, tool_name, args, execution_permit=decision.execution_permit, effect_id=effect_id
    )


def _live_query_trace(monkeypatch) -> tuple[dict, dict]:
    # This unit suite verifies the deterministic projection contract.  A real
    # key in the developer .env must not silently turn it into a network/model
    # integration test; configured-model browser gates own that evidence.
    monkeypatch.setenv("ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE", "candidate")
    business = _Business()
    monkeypatch.setattr(ecommerce_context, "get_business_port", lambda: business)
    state = _state()
    result = _dispatch(state, "list_orders", _query_args())
    assert result["ok"] is True
    return state, {"name": "list_orders", "call_id": "call-order-list", "result": result}


def test_contract_manifest_declares_domain_owned_semantics_without_core_field_rules():
    assert ORDER_LIST_CONTRACT["contract_id"] == "commerce.order_list@1"
    assert ORDER_LIST_CONTRACT["contract_owner"] == "ecommerce_overlay"
    assert ORDER_LIST_CONTRACT["projection_boundary"] == "ecommerce_order_list_projector"
    assert ORDER_LIST_CONTRACT["adequacy"]["required_visible_semantics"] == [
        "resource_identity",
        "resource_display_label",
    ]
    assert ORDER_LIST_CONTRACT["payload"]["item_required_fields"] == {
        "order_id": "resource_identity",
        "product_name": "resource_display_label",
    }


def test_live_query_keeps_verified_identity_until_the_single_projection_boundary(monkeypatch):
    _state_value, trace = _live_query_trace(monkeypatch)
    observations = trace["result"]["data"]["orders"]

    assert observations == [
        {
            "reference_handle": observations[0]["reference_handle"],
            "order_id": "10004",
            "product_name": "定制马克杯",
            "status": "已签收",
            "amount": 59.0,
        },
        {
            "reference_handle": observations[1]["reference_handle"],
            "order_id": "10003",
            "product_name": "无线鼠标",
            "status": "待发货",
            "amount": 99.0,
        },
    ]
    assert all("label" not in row and "title" not in row for row in observations)

    blocks = build_response_blocks({"tool_trace": [trace]}, registry=PresentationRegistry([EcommerceObservationAdapter()]))
    assert len(blocks) == 1
    block = blocks[0]
    assert block["contract_id"] == "commerce.order_list@1"
    assert block["contract_version"] == 1
    assert block["contract_owner"] == "ecommerce_overlay"
    assert block["projection_boundary"] == "ecommerce_order_list_projector"
    assert [row["order_id"] for row in block["items"]] == ["10004", "10003"]
    assert [row["product_name"] for row in block["items"]] == ["定制马克杯", "无线鼠标"]


def test_business_record_query_preserves_a_verified_collection_scope(monkeypatch):
    business = _Business()
    monkeypatch.setattr(ecommerce_context, "get_business_port", lambda: business)
    state = {
        **_state(),
        "current_user_input": "查所有售后工单",
    }

    result = _dispatch(state, "list_after_sales_requests", {
        "target": {"mode": "all_orders"},
        "expected_shape": "collection",
        "reference_span": "查所有售后工单",
    })

    assert result["ok"] is True
    assert result["data"]["count"] == 1
    assert result["data"]["items"] == [{
        "record_reference": "AS-10004",
        "record_kind": "after_sales",
        "status": "处理中",
        "updated_at": "2026-07-15T10:00:00Z",
        "order_id": "10004",
    }]
    assert result["data"]["query_target"]["resource_type"] == "collection"
    assert result["data"]["query_target"]["label"] == "当前订单范围（2个对象）"
    assert [row["order_id"] for row in result["data"]["query_targets"]] == ["10004", "10003"]


def test_api_and_serialized_sse_result_preserve_the_same_canonical_block(monkeypatch):
    _state_value, trace = _live_query_trace(monkeypatch)
    graph_result = {
        "runtime_outcome": trace["result"]["runtime_outcome"],
        "tool_trace": [trace],
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_thread_id": "presentation-thread",
    }

    response = ResponseProjector(message_store=None).normalize("presentation-thread", graph_result)
    assert response.presentation_mode == "structured"
    assert len(response.blocks) == 1
    api_payload = response.model_dump()
    # ConversationTurnService puts this exact payload in its final SSE `result`
    # event; JSON round-trip proves no API/SSE mapper renames the fields.
    sse_result_payload = json.loads(json.dumps(api_payload, ensure_ascii=False))
    block = sse_result_payload["blocks"][0]
    assert block["contract_id"] == "commerce.order_list@1"
    assert block["items"][0]["order_id"] == "10004"
    assert block["items"][0]["product_name"] == "定制马克杯"
    assert block["items"][0]["status"] == "已签收"
    assert block["items"][0]["amount"] == 59.0


def test_missing_user_identity_is_a_controlled_violation_not_a_status_only_success():
    malformed_trace = {
        "name": "list_orders",
        "call_id": "call-malformed",
        "result": {
            "ok": True,
            "data": {
                "orders": [
                    {"reference_handle": "artifact:order:10004", "label": "定制马克杯（订单 10004）", "status": "已签收", "amount": 59.0},
                    {"reference_handle": "artifact:order:10003", "label": "无线鼠标（订单 10003）", "status": "待发货", "amount": 99.0},
                ],
            },
        },
    }
    result = {"tool_trace": [malformed_trace]}
    blocks = build_response_blocks(result, registry=PresentationRegistry([EcommerceObservationAdapter()]))

    assert [block["type"] for block in blocks] == ["projection_contract_violation"]
    violation = blocks[0]["contract_violation"]
    assert violation["contract_id"] == "commerce.order_list@1"
    assert set(violation["missing_required_semantics"]) >= {"resource_identity", "resource_display_label"}
    assert result["presentation_contract_violations"] == [violation]
    assert "已签收" not in blocks[0]["content"]


def test_verified_empty_business_status_query_releases_registered_primary_block():
    """A zero-row query is a result; it must not become an unknown contract."""
    trace = {
        "name": "list_refunds",
        "call_id": "call-empty-refunds",
        "result": {
            "ok": True,
            "data": {
                "capability": "ecommerce.refunds.list",
                "items": [],
                "count": 0,
                "query_target": {
                    "order_id": "10002",
                    "product_name": "机械键盘",
                    "label": "机械键盘（订单 10002）",
                },
            },
            "runtime_outcome": {
                "outcome_type": "query",
                "effects": "none",
                "safe_to_continue": False,
                "customer_safe_summary": "已完成已验证查询，共 0 项。",
                "next_interaction": "none",
                "payload": {"items": [], "count": 0},
            },
        },
    }
    result = {"tool_trace": [trace]}

    blocks = build_response_blocks(
        result,
        registry=PresentationRegistry([EcommerceObservationAdapter()]),
    )

    assert len(blocks) == 1
    assert blocks[0]["contract_id"] == "commerce.business_status_list@1"
    assert blocks[0]["title"] == "退款记录"
    assert blocks[0]["summary"] == "暂未找到业务记录。"
    assert blocks[0]["items"] == []
    assert blocks[0]["target_order_id"] == "10002"
    assert blocks[0]["target_product_name"] == "机械键盘"
    assert blocks[0]["coverage"]["resolved_member_count"] == 0
    assert result.get("presentation_contract_violations") is None


def test_registered_eligibility_listing_has_a_real_scope_bound_overlay_implementation():
    from agent_core.ledger import eligibility_entry, scope_for_state

    state = _state()
    state["artifact_ledger"] = [
        eligibility_entry(
            action_id="refund_request",
            operation="APPLY_REFUND",
            target_handle="artifact:order:10003",
            input_values={},
            preview={"decision": "ALLOWED"},
            scope=scope_for_state(state),
            turn=7,
            label="无线鼠标退款资格",
        )
    ]

    result = _dispatch(state, "list_active_eligibilities", {})

    assert result["ok"] is True
    assert result["data"]["eligibilities"] == [
        {
            "handle": state["artifact_ledger"][0]["handle"],
            "label": "无线鼠标退款资格",
            "action_id": "refund_request",
            "operation": "APPLY_REFUND",
            "target_handle": "artifact:order:10003",
            "status": "eligible",
            "expires_at": state["artifact_ledger"][0]["expires_at"],
        }
    ]


def test_grounded_fallback_reads_current_user_input_without_loop_import_cycle():
    from agent_core.presentation.grounded import render_grounded_tool_answer

    assert "您好" in render_grounded_tool_answer({"current_user_input": "你好", "tool_trace": []})


def test_conversation_sse_result_keeps_the_canonical_block_without_a_mapper_rewrite(monkeypatch):
    """Exercise the real ConversationTurnService SSE final-result boundary."""
    from contextlib import contextmanager

    from app.schemas.chat_schema import ChatRequest
    from app.use_cases.conversation_turn import ConversationTurnService

    _state_value, trace = _live_query_trace(monkeypatch)
    graph_result = {
        "runtime_outcome": trace["result"]["runtime_outcome"],
        "tool_trace": [trace],
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_thread_id": "presentation-thread",
    }

    class _Noop:
        def add_message(self, *_args, **_kwargs):
            return None

        def upsert_thread(self, *_args, **_kwargs):
            return None

        def log_event(self, *_args, **_kwargs):
            return None

    class _Snapshot:
        values = graph_result

    class _Graph:
        def stream(self, *_args, **_kwargs):
            return iter(())

        def get_state(self, _config):
            return _Snapshot()

    class _PublicUpdates:
        def project_public_update(self, _update):
            return None

    class _Service:
        graph = _Graph()
        message_store = _Noop()
        thread_store = _Noop()
        trace_logger = _Noop()
        sse_stream_adapter = _PublicUpdates()

        def _claim_or_validate_thread(self, *_args):
            return None

        @contextmanager
        def _serialized_turn(self, *_args):
            yield {"wait_ms": 0, "assert_valid": lambda: None}

        def _config_for_request(self, *_args):
            return {"configurable": {}}

        def _human_message(self, message):
            return message

        def _require_graph(self):
            return self.graph

        def _normalize(self, thread_id, result, *, include_debug=False):
            return ResponseProjector(message_store=None).normalize(
                thread_id,
                result,
                include_debug=include_debug,
            )

        def _persist_public_response(self, *_args, **_kwargs):
            return None

        def _debug_snapshot(self, result):
            return result

    events = list(
        ConversationTurnService(_Service()).stream(
            ChatRequest(
                thread_id="presentation-thread",
                user_id="u001",
                tenant_id="tenant-a",
                role="customer",
                message="我买了什么",
            )
        )
    )
    result_event = next(event for event in events if event.startswith("event: result\n"))
    payload = json.loads(result_event.split("data: ", 1)[1])
    block = payload["blocks"][0]

    assert block["contract_id"] == "commerce.order_list@1"
    assert block["items"][0]["order_id"] == "10004"
    assert block["items"][0]["product_name"] == "定制马克杯"
