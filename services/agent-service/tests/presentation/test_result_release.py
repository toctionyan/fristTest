from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy

from app.schemas.chat_schema import ChatRequest
from app.services.response_projector import ResponseProjector
from app.use_cases.conversation_turn import ConversationTurnService
from agent_core.ledger import append_entries
from agent_core.context.visible_result_refs import mark_visible_result_refs
from agent_core.presentation.registry import PresentationRegistry, build_response_blocks, default_presentation_registry
from agent_core.composition import get_runtime_registry
from agent_core.runtime.capability_gate import issue_execution_permit
from agent_core.runtime.semantic_capability_verifier import CandidateOnlySemanticVerifier
from agent_modules.ecommerce.presentation.adapter import EcommerceObservationAdapter
from agent_modules.ecommerce.presentation.contracts import presentation_contract_manifests
from agent_core.presentation.contracts.release_gate import StructuredResultReleaseGate
from agent_core.presentation.contracts.runtime import runtime_presentation_contract_manifests
from agent_core.presentation.contracts.governance import PresentationContractRegistry
from agent_core.presentation.contracts.renderer_registry import RendererRegistry
from agent_core.runtime.outcomes import outcome
from agent_core.runtime.answer_release_alignment import CandidateOnlyAnswerAlignmentVerifier
from agent_modules.ecommerce import _shared_execution as ecommerce_execution
from agent_modules.ecommerce.shared import context as ecommerce_context


class _Business:
    def __init__(self) -> None:
        self.rows = [
            {
                "order_id": "10003",
                "product_name": "无线鼠标",
                "status": "待发货",
                "amount": 99.0,
                "version": 3,
            },
            {
                "order_id": "10001",
                "product_name": "蓝牙耳机",
                "status": "已发货",
                "amount": 199.0,
                "version": 4,
            },
        ]

    def query_resources(self, _actor, *, resource_type, query_spec):
        assert resource_type == "order"
        assert query_spec.get("user_id") == "u001"
        return {"success": True, "data": deepcopy(self.rows)}

    def read_resource(self, _actor, *, resource_type, resource_id, query=None):
        if resource_type == "order":
            assert (query or {}).get("user_id") == "u001"
            return {"success": True, "data": deepcopy(next(row for row in self.rows if row["order_id"] == resource_id))}
        if resource_type == "logistics":
            data = {
                "10003": {
                    "status": "待发货",
                    "latest": "商家正在备货",
                    "estimate": "预计 24 小时内发货",
                },
                "10001": {
                    "status": "运输中",
                    "latest": "已到达 Phoenix 分拨中心",
                    "estimate": "预计 2 天内送达",
                },
                "10004": {"status": "已签收", "latest": "用户已签收", "estimate": "已送达"},
                "10002": {"status": "已签收", "latest": "用户已签收", "estimate": "已送达"},
            }
            return {"success": True, "data": deepcopy(data[resource_id])}
        raise AssertionError(f"unexpected resource type {resource_type}")



class _FourOrderBusiness(_Business):
    """Four real-looking facts for the same contextual range shown in the UI report.

    This deliberately verifies that a set difference keeps its two remaining
    members all the way through the formal logistics presentation contract.
    """

    def __init__(self) -> None:
        self.rows = [
            {"order_id": "10004", "product_name": "定制马克杯", "status": "已签收", "amount": 59.0, "version": 1},
            {"order_id": "10003", "product_name": "无线鼠标", "status": "待发货", "amount": 99.0, "version": 2},
            {"order_id": "10002", "product_name": "机械键盘", "status": "已签收", "amount": 399.0, "version": 3},
            {"order_id": "10001", "product_name": "蓝牙耳机", "status": "已发货", "amount": 199.0, "version": 4},
        ]



def _state(message: str = "它们都到哪了？") -> dict:
    return {
        "current_thread_id": "v183-thread",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_role": "customer",
        "current_user_input": message,
        "turn_index": 9,
        "artifact_ledger": [],
    }


def _all_orders_args() -> dict:
    return {
        "target": {"mode": "all_orders"},
        "expected_shape": "collection",
        "reference_span": "它们",
    }


def _dispatch(state: dict, tool_name: str, args: dict) -> dict:
    # Presentation tests exercise the projection boundary, not a live model.
    # Inject the explicit local/test verifier so an API key in .env cannot
    # silently turn these unit tests into network-dependent integration tests.
    state.setdefault("semantic_capability_verifier", CandidateOnlySemanticVerifier())
    registry = get_runtime_registry().capabilities
    effect_id = f"v183:{tool_name}:{state.get('turn_index', 0)}"
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


def _trace_from_live_logistics(monkeypatch) -> tuple[dict, dict]:
    business = _Business()
    monkeypatch.setattr(ecommerce_context, "get_business_port", lambda: business)
    state = _state()
    result = _dispatch(state, "get_order_logistics", _all_orders_args())
    assert result["ok"] is True
    return state, {"name": "get_order_logistics", "call_id": "call-v183-logistics", "result": result}


def _conversation_result_event(graph_result: dict) -> dict:
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
            return ResponseProjector(message_store=None).normalize(thread_id, result, include_debug=include_debug)

        def _persist_public_response(self, *_args, **_kwargs):
            return None

        def _debug_snapshot(self, result):
            return result

    events = list(
        ConversationTurnService(_Service()).stream(
            ChatRequest(
                thread_id="v183-thread",
                user_id="u001",
                tenant_id="tenant-a",
                role="customer",
                message="它们都到哪了？",
            )
        )
    )
    result_event = next(event for event in events if event.startswith("event: result\n"))
    return json.loads(result_event.split("data: ", 1)[1])


def test_contextual_difference_to_logistics_preserves_exact_remaining_collection_to_contract(monkeypatch):
    """Regression for: all orders → signed subset → remaining → their logistics.

    It checks the actual Resolver/Ledger/Capability path, not a hand-written
    display fixture, so a hidden range shrink or a legacy raw block cannot
    silently pass as a valid logistics result.
    """

    business = _FourOrderBusiness()
    monkeypatch.setattr(ecommerce_context, "get_business_port", lambda: business)

    all_state = {**_state("我都买了什么？"), "turn_index": 1}
    all_result = _dispatch(
        all_state,
        "list_orders",
        {
            "target": {"mode": "all_orders"},
            "expected_shape": "collection",
            "reference_span": "我都买了什么",
        },
    )
    assert all_result["ok"] is True
    assert all_result["data"]["count"] == 4
    ledger = append_entries([], all_result["ledger_entries"])
    all_view = all_result["data"]["view_handle"]
    # The next turn can consume this collection only after it crossed the
    # customer-visible release boundary; merely existing in the ledger is not enough.
    ledger = mark_visible_result_refs(
        ledger,
        state=all_state,
        evidence_handles=[all_view],
    )

    signed_state = {**_state("已经签收的有哪些？"), "turn_index": 2, "artifact_ledger": ledger}
    signed_result = _dispatch(
        signed_state,
        "list_orders",
        {
            "target": {
                "mode": "set_operation",
                "operator": "filter",
                "left_handle": all_view,
                "status": "已签收",
                "status_span": "已经签收",
            },
            "expected_shape": "collection",
            "reference_span": "已经签收的",
        },
    )
    assert signed_result["ok"] is True
    assert {row["product_name"] for row in signed_result["data"]["orders"]} == {"定制马克杯", "机械键盘"}
    ledger = append_entries(ledger, signed_result["ledger_entries"])
    ledger = mark_visible_result_refs(
        ledger,
        state=signed_state,
        evidence_handles=[signed_result["data"]["view_handle"]],
    )

    remaining_state = {**_state("其余的呢？"), "turn_index": 3, "artifact_ledger": ledger}
    remaining_result = _dispatch(
        remaining_state,
        "list_orders",
        {
            "target": {
                "mode": "set_operation",
                "operator": "difference",
                "left_handle": all_view,
                "right_handle": signed_result["data"]["view_handle"],
            },
            "expected_shape": "collection",
            "reference_span": "其余的",
        },
    )
    assert remaining_result["ok"] is True
    assert {row["product_name"] for row in remaining_result["data"]["orders"]} == {"无线鼠标", "蓝牙耳机"}
    ledger = append_entries(ledger, remaining_result["ledger_entries"])
    ledger = mark_visible_result_refs(
        ledger,
        state=remaining_state,
        evidence_handles=[remaining_result["data"]["view_handle"]],
    )

    logistics_state = {**_state("它们都到哪了？"), "turn_index": 4, "artifact_ledger": ledger}
    logistics_result = _dispatch(
        logistics_state,
        "get_order_logistics",
        {
            "target": {"mode": "collection", "left_handle": remaining_result["data"]["view_handle"]},
            "expected_shape": "collection",
            "reference_span": "它们",
        },
    )
    assert logistics_result["ok"] is True
    assert [(row["order"]["order_id"], row["logistics"]["latest"]) for row in logistics_result["data"]["items"]] == [
        ("10003", "商家正在备货"),
        ("10001", "已到达 Phoenix 分拨中心"),
    ]

    trace = [{"name": "get_order_logistics", "call_id": "contextual-difference-logistics", "result": logistics_result}]
    block = build_response_blocks({"tool_trace": trace}, registry=PresentationRegistry([EcommerceObservationAdapter()]))[0]
    assert block["contract_id"] == "commerce.logistics_overview@1"
    assert block["coverage"]["resolved_member_count"] == 2
    assert block["coverage"]["presented_member_count"] == 2
    assert [(row["order_id"], row["product_name"], row["latest"]) for row in block["items"]] == [
        ("10003", "无线鼠标", "商家正在备货"),
        ("10001", "蓝牙耳机", "已到达 Phoenix 分拨中心"),
    ]


def test_inventory_contracts_cover_all_formal_structured_and_transaction_result_types():
    manifests = {row["contract_id"] for row in [*presentation_contract_manifests(), *runtime_presentation_contract_manifests()]}
    assert manifests == {
        "commerce.order_list@1",
        "commerce.logistics_overview@1",
        "commerce.business_status_list@1",
        "commerce.next_actions@1",
        "commerce.eligibility_decision@1",
        "commerce.advisory@1",
        "runtime.transaction_status@1",
        "runtime.interaction_timeline@1",
        "runtime.resource_list@1",
    }


def test_live_logistics_query_reaches_canonical_contract_and_sse_without_blank_fallback(monkeypatch):
    _state_value, trace = _trace_from_live_logistics(monkeypatch)
    result = {
        "runtime_outcome": trace["result"]["runtime_outcome"],
        "tool_trace": [trace],
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_thread_id": "v183-thread",
        # This unit test owns only the deterministic presentation boundary.
        # Never spend a configured provider call to reinterpret a hand-built
        # graph fixture that deliberately omits the live request envelope.
        "answer_alignment_verifier": CandidateOnlyAnswerAlignmentVerifier(),
    }

    response = ResponseProjector(message_store=None).normalize("v183-thread", result)
    assert response.presentation_mode == "structured"
    assert response.answer is None
    assert len(response.blocks) == 1
    block = response.blocks[0]
    assert block["contract_id"] == "commerce.logistics_overview@1"
    assert block["coverage"] == {
        "mode": "full",
        "source_population": "requested_result_population",
        "status": "complete",
        "resolved_member_count": 2,
        "presented_member_count": 2,
        "presented_population_proof": "business_matched_member_identity_set",
    }
    assert [(item["order_id"], item["product_name"], item["latest"]) for item in block["items"]] == [
        ("10003", "无线鼠标", "商家正在备货"),
        ("10001", "蓝牙耳机", "已到达 Phoenix 分拨中心"),
    ]

    payload = _conversation_result_event(result)
    delivered = payload["blocks"][0]
    assert delivered["contract_id"] == "commerce.logistics_overview@1"
    assert delivered["items"][0]["product_name"] == "无线鼠标"
    assert delivered["items"][0]["latest"] == "商家正在备货"
    assert payload["answer"] is None


def test_related_status_and_next_action_traces_are_projected_by_registered_contracts():
    trace = [
        {
            "name": "list_refunds",
            "call_id": "call-status",
            "result": {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "record_reference": "refund-10001",
                            "record_kind": "refund",
                            "status": "处理中",
                            "updated_at": "2026-07-06 10:00",
                            "order_id": "10001",
                        }
                    ],
                },
            },
        }
    ]
    status_block = build_response_blocks({"tool_trace": trace}, registry=PresentationRegistry([EcommerceObservationAdapter()]))[0]
    assert status_block["contract_id"] == "commerce.business_status_list@1"
    assert status_block["items"][0]["record_reference"] == "refund-10001"
    assert status_block["coverage"]["mode"] == "full"
    status_result = {
        "runtime_outcome": outcome("query", correlation_id="status-v183", customer_safe_summary="已查询退款记录。", payload={}).as_dict(),
        "tool_trace": trace,
        "answer_alignment_verifier": CandidateOnlyAnswerAlignmentVerifier(),
    }
    status_sse = _conversation_result_event(status_result)
    assert status_sse["answer"] is None
    assert status_sse["blocks"][0]["contract_id"] == "commerce.business_status_list@1"
    assert status_sse["blocks"][0]["items"][0]["record_reference"] == "refund-10001"

    action_trace = [
        {
            "name": "consult_warranty_policy",
            "call_id": "call-actions",
            "result": {
                "ok": True,
                "data": {
                    "capability": "orders.issue_consultation",
                    "order": {"order_id": "10001", "product_name": "蓝牙耳机"},
                    "question": "保修政策是什么",
                    "knowledge_available": True,
                    "policy_evidence": [{"title": "保修政策", "content": "数码配件享受厂家保修。", "source": "内置保修政策"}],
                },
            },
        }
    ]
    action_block = build_response_blocks({"tool_trace": action_trace}, registry=PresentationRegistry([EcommerceObservationAdapter()]))[0]
    assert action_block["contract_id"] == "commerce.advisory@1"
    assert action_block["target_order_id"] == "10001"
    assert action_block["summary"] == "数码配件享受厂家保修。"
    assert "actions" not in action_block
    action_result = {
        "runtime_outcome": outcome("query", correlation_id="actions-v183", customer_safe_summary="已核验可办理选项。", payload={}).as_dict(),
        "tool_trace": action_trace,
        "answer_alignment_verifier": CandidateOnlyAnswerAlignmentVerifier(),
    }
    action_sse = _conversation_result_event(action_result)
    assert action_sse["answer"] is None
    assert action_sse["blocks"][0]["contract_id"] == "commerce.advisory@1"
    assert action_sse["blocks"][0]["target_order_id"] == "10001"


def test_transaction_status_is_a_core_contract_not_a_legacy_raw_block():
    runtime = outcome(
        "transaction_status",
        correlation_id="tx-v183",
        customer_safe_summary="退款申请正在处理中。",
        payload={"draft": {"draft_state": "COMMITTING"}},
    ).as_dict()
    response = ResponseProjector(message_store=None).normalize("v183-thread", {"runtime_outcome": runtime})

    assert response.presentation_mode == "transaction_status"
    assert response.answer is None
    assert response.blocks[0]["contract_id"] == "runtime.transaction_status@1"
    assert response.blocks[0]["coverage"]["status"] == "not_applicable"
    transaction_sse = _conversation_result_event({"runtime_outcome": runtime})
    assert transaction_sse["answer"] is None
    assert transaction_sse["presentation_mode"] == "transaction_status"
    assert transaction_sse["blocks"][0]["contract_id"] == "runtime.transaction_status@1"


def test_release_gate_rejects_legacy_formal_block_and_coverage_mismatch():
    registry = default_presentation_registry()
    legacy = registry.release_blocks(
        [{"type": "logistics_overview", "role": "primary", "title": "物流总览", "items": []}],
        trace_id="legacy-v183",
        require_primary=True,
    )
    assert legacy[0]["type"] == "projection_contract_violation"
    assert "registered_presentation_contract" in legacy[0]["contract_violation"]["missing_required_semantics"]

    bad_coverage = {
        "type": "order_list",
        "role": "primary",
        "contract_id": "commerce.order_list@1",
        "contract_version": 1,
        "contract_owner": "ecommerce_overlay",
        "projection_boundary": "ecommerce_order_list_projector",
        "producer": "ecommerce.orders.list.capability",
        "title": "订单（1）",
        "summary": "已找到 1 笔订单。",
        "degradation": {"level": "none", "missing_optional_semantics": []},
        "items": [{"order_id": "10001", "product_name": "蓝牙耳机", "status": "已发货", "amount": 199}],
        "coverage": {
            "mode": "full",
            "source_population": "resolved_order_collection",
            "status": "complete",
            "resolved_member_count": 2,
            "presented_member_count": 1,
            "presented_population_proof": "same_member_identity_set",
        },
    }
    rejected = registry.release_blocks([bad_coverage], trace_id="coverage-v183", require_primary=True)
    assert rejected[0]["type"] == "projection_contract_violation"
    assert "coverage_population_mismatch" in rejected[0]["contract_violation"]["missing_required_semantics"]


def test_unmapped_query_trace_and_no_blocks_fail_closed_instead_of_blank_structured_response():
    result = {
        "runtime_outcome": outcome(
            "query",
            correlation_id="unmapped-v183",
            customer_safe_summary="已完成查询。",
            payload={},
        ).as_dict(),
        "tool_trace": [{"name": "unknown_query", "call_id": "unknown-call", "result": {"ok": True, "data": {"capability": "unknown.query"}}}],
    }
    response = ResponseProjector(message_store=None).normalize("v183-thread", result)
    assert response.presentation_mode == "notice"
    assert response.answer is None
    assert response.blocks[0]["type"] == "notice"
    assert any(marker in response.blocks[0]["content"] for marker in ("无法完整展示", "未能证明当前结果"))
    assert "registered_primary_presentation" not in str(response.blocks)
    assert "registered_primary_presentation" in str(result["presentation_contract_violations"])


def test_contract_registry_is_only_a_gate_and_does_not_allow_unregistered_renderer_registration():
    contracts = PresentationContractRegistry([*presentation_contract_manifests(), *runtime_presentation_contract_manifests()])
    gate = StructuredResultReleaseGate(contracts, RendererRegistry())
    block = {
        "type": "transaction_status",
        "role": "primary",
        "contract_id": "runtime.transaction_status@1",
        "contract_version": 1,
        "contract_owner": "runtime_transaction_projection",
        "projection_boundary": "runtime_transaction_status_projector",
        "producer": "runtime.transaction_status.outcome",
        "summary": "处理中",
        "data": {"draft": {"draft_state": "COMMITTING"}},
        "degradation": {"level": "none", "missing_optional_semantics": []},
        "coverage": {"mode": "not_collection", "source_population": "runtime_transaction_outcome", "status": "not_applicable"},
    }
    decision = gate.release([block], trace_id="no-renderer", require_primary=True)
    assert decision.released is False
    assert decision.blocks[0]["type"] == "projection_contract_violation"
    assert "registered_channel_renderer" in decision.violation.missing_required_semantics
