from __future__ import annotations

from tests.support.paths import agent_root

import sqlite3
from pathlib import Path

from agent_core.persistence.message_store import MessageStore
from agent_core.presentation.registry import PresentationRegistry, build_response_blocks
from agent_modules.ecommerce.presentation.adapter import EcommerceObservationAdapter


def _logistics_trace() -> list[dict]:
    return [
        {
            "name": "list_orders",
            "result": {
                "ok": True,
                "data": {
                    "orders": [
                        {"order_id": "10001", "product_name": "蓝牙耳机", "amount": 199, "status": "已发货"},
                        {"order_id": "10003", "product_name": "无线鼠标", "amount": 99, "status": "待发货"},
                    ],
                },
            },
        },
        {
            "name": "get_order_logistics",
            "result": {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "order": {"order_id": "10001", "product_name": "蓝牙耳机", "status": "已发货"},
                            "logistics": {"status": "运输中", "latest": "已到达 Phoenix 分拨中心", "estimate": "预计 2 天内送达"},
                        },
                        {
                            "order": {"order_id": "10003", "product_name": "无线鼠标", "status": "待发货"},
                            "logistics": {"status": "待发货", "latest": "商家正在备货", "estimate": "预计 24 小时内发货"},
                        },
                    ],
                },
            },
        },
    ]


def test_one_user_goal_produces_exactly_one_primary_view_without_duplicate_order_list():
    blocks = build_response_blocks({"tool_trace": _logistics_trace()}, answer="物流结果：不应被重复展示。", registry=PresentationRegistry([EcommerceObservationAdapter()]))

    assert [block["type"] for block in blocks] == ["logistics_overview"]
    view = blocks[0]
    assert view["role"] == "primary"
    assert "order_list" not in str(blocks)
    assert [row["status"] for row in view["items"]] == ["运输中", "待发货"]
    assert all("order_status" not in row and "logistics_status" not in row for row in view["items"])




def test_independent_user_goals_release_one_primary_block_each():
    trace = [
        {
            "name": "get_order_details",
            "goal_ids": ["goal:order-details"],
            "result": {
                "ok": True,
                "data": {
                    "order": {
                        "order_id": "10002",
                        "product_name": "机械键盘",
                        "status": "已签收",
                        "amount": 399.0,
                    }
                },
            },
        },
        {
            "name": "list_invoices",
            "goal_ids": ["goal:invoice-status"],
            "result": {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "record_reference": "INV-10002",
                            "record_kind": "invoice",
                            "order_id": "10002",
                            "status": "已开具",
                            "updated_at": "2026-07-27T00:00:00Z",
                        }
                    ],
                    "query_target": {
                        "order_id": "10002",
                        "label": "订单 10002",
                    },
                },
            },
        },
    ]

    blocks = build_response_blocks(
        {"tool_trace": trace},
        registry=PresentationRegistry([EcommerceObservationAdapter()]),
    )

    assert [block["contract_id"] for block in blocks] == [
        "commerce.order_list@1",
        "commerce.business_status_list@1",
    ]
    assert all("_goal_ids" not in block and "_presentation_order" not in block for block in blocks)


def test_same_goal_still_suppresses_prerequisite_primary_view():
    trace = [
        {**_logistics_trace()[0], "goal_ids": ["goal:logistics"]},
        {**_logistics_trace()[1], "goal_ids": ["goal:logistics"]},
    ]

    blocks = build_response_blocks(
        {"tool_trace": trace},
        registry=PresentationRegistry([EcommerceObservationAdapter()]),
    )

    assert [block["contract_id"] for block in blocks] == ["commerce.logistics_overview@1"]


def test_presentation_registry_fail_closes_an_unregistered_primary_block():
    class LegacyPrimary:
        adapter_id = "legacy-primary"

        def blocks_from_trace(self, _trace):
            return [{"type": "summary", "role": "primary", "content": "primary"}]

    registry = PresentationRegistry([LegacyPrimary()])
    blocks = registry.compose([{"name": "anything"}])
    assert [block["type"] for block in blocks] == ["projection_contract_violation"]
    assert "registered_presentation_contract" in blocks[0]["contract_violation"]["missing_required_semantics"]


def test_issue_consultation_projects_read_only_advisory_without_unrequested_actions():
    trace = [
        {
            "name": "consult_invoice_policy",
            "result": {
                "ok": True,
                "data": {
                    "capability": "orders.issue_consultation",
                    "order": {"order_id": "10004", "product_name": "定制马克杯"},
                    "question": "能开发票吗",
                    "knowledge_available": True,
                    "policy_evidence": [{"title": "开票政策", "content": "已支付订单可以申请电子发票。", "source": "内置开票政策"}],
                    "consultation_only": True,
                },
            },
        }
    ]

    blocks = build_response_blocks({"tool_trace": trace}, answer="本次仅作咨询。", registry=PresentationRegistry([EcommerceObservationAdapter()]))

    assert blocks[0]["type"] == "advisory"
    assert blocks[0]["contract_id"] == "commerce.advisory@1"
    assert blocks[0]["target_order_id"] == "10004"
    assert blocks[0]["target_product_name"] == "定制马克杯"
    assert blocks[0]["summary"] == "已支付订单可以申请电子发票。"
    assert "actions" not in blocks[0]
    assert "申请退款" not in str(blocks)
    assert "申请售后" not in str(blocks)


def test_consultation_presentation_supersedes_its_order_detail_prerequisite():
    trace = [
        {
            "name": "get_order_details",
            "result": {
                "ok": True,
                "data": {
                    "order": {
                        "order_id": "10004",
                        "product_name": "定制马克杯",
                        "status": "已签收",
                        "amount": 59.0,
                    },
                },
            },
        },
        {
            "name": "consult_invoice_policy",
            "result": {
                "ok": True,
                "data": {
                    "order": {"order_id": "10004", "product_name": "定制马克杯"},
                    "question": "订单10004能开发票吗？",
                    "knowledge_available": True,
                    "policy_evidence": [{
                        "title": "开票政策",
                        "content": "已支付且未全额退款的订单可以申请电子发票。",
                        "source": "内置开票政策",
                    }],
                },
            },
        },
    ]

    blocks = build_response_blocks(
        {"tool_trace": trace},
        registry=PresentationRegistry([EcommerceObservationAdapter()]),
    )

    assert [block["type"] for block in blocks] == ["advisory"]
    assert blocks[0]["target_order_id"] == "10004"
    assert "发票" in blocks[0]["summary"]
    assert "order_list" not in str(blocks)


def test_order_details_projects_the_verified_single_order_instead_of_an_unrelated_action_card():
    trace = [
        {
            "name": "get_order_details",
            "result": {
                "ok": True,
                "data": {
                    "capability_key": "ecommerce.order.details",
                    "order": {
                        "order_id": "10001",
                        "product_name": "蓝牙耳机",
                        "status": "已发货",
                        "amount": 199.0,
                    },
                },
            },
        }
    ]

    blocks = build_response_blocks({"tool_trace": trace}, registry=PresentationRegistry([EcommerceObservationAdapter()]))

    assert [block["type"] for block in blocks] == ["order_list"]
    assert blocks[0]["contract_id"] == "commerce.order_list@1"
    assert blocks[0]["items"] == [
        {"order_id": "10001", "product_name": "蓝牙耳机", "status": "已发货", "amount": 199.0}
    ]


def test_refund_eligibility_projects_decision_and_apply_refund_action():
    trace = [
        {
            "name": "evaluate_refund_eligibility",
            "result": {
                "ok": True,
                "data": {
                    "capability": "refund_eligibility",
                    "eligible": True,
                    "target_label": "蓝牙耳机",
                    "preview": {"snapshot": {"order_id": "10001", "product_name": "蓝牙耳机"}},
                },
            },
        }
    ]

    blocks = build_response_blocks({"tool_trace": trace}, registry=PresentationRegistry([EcommerceObservationAdapter()]))

    assert blocks[0]["type"] == "eligibility_decision"
    assert blocks[0]["contract_id"] == "commerce.eligibility_decision@1"
    assert blocks[0]["title"] == "退款资格已通过"
    assert blocks[0]["eligible"] is True
    assert blocks[0]["decision"] == "ALLOWED"
    assert len(blocks[0]["actions"]) == 1
    action = blocks[0]["actions"][0]
    assert action["action_id"] == "create_refund"
    assert action["label"] == "申请退款"
    assert action["target"] == {"resource_type": "order", "order_id": "10001"}
    assert action["input_hints"] == {}


def test_blocked_refund_eligibility_still_projects_authoritative_decision_without_fake_action():
    trace = [
        {
            "name": "evaluate_refund_eligibility",
            "result": {
                "ok": True,
                "data": {
                    "eligible": False,
                    "preview": {
                        "decision": "BLOCKED",
                        "message": "订单已发货但尚未签收，当前不能申请退款。",
                        "snapshot": {"order_id": "10001", "product_name": "蓝牙耳机"},
                    },
                },
            },
        }
    ]

    blocks = build_response_blocks({"tool_trace": trace}, registry=PresentationRegistry([EcommerceObservationAdapter()]))

    assert blocks[0]["type"] == "eligibility_decision"
    assert blocks[0]["title"] == "退款资格暂未通过"
    assert blocks[0]["summary"] == "订单已发货但尚未签收，当前不能申请退款。"
    assert blocks[0]["target_order_id"] == "10001"
    assert blocks[0]["eligible"] is False
    assert blocks[0]["actions"] == []


def test_message_store_additively_migrates_old_schema_and_replays_public_envelope(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, role TEXT, content TEXT, created_at TEXT)"
    )
    conn.execute("INSERT INTO messages(thread_id, role, content, created_at) VALUES('legacy', 'assistant', '旧消息', '2026-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()

    store = MessageStore(db_path)
    try:
        store.add_message(
            "thread-1",
            "assistant",
            "共 2 笔订单。",
            message_type="answer",
            presentation=[{"type": "order_list", "role": "primary", "items": [{"id": "10001", "title": "蓝牙耳机"}]}],
            interaction={
                "interaction_id": "h_offer:demo",
                "kind": "transaction",
                "lifecycle": "committed",
                "title": "取消订单",
                "target": "无线鼠标（订单 10003）",
                "actions": [],
                "control": {},
                "read_only": True,
            },
        )
        rows = store.list_messages("thread-1")
        assert rows[0]["message_type"] == "answer"
        assert rows[0]["presentation"][0]["type"] == "order_list"
        assert rows[0]["interaction"]["lifecycle"] == "committed"
        old = store.list_messages("legacy")
        assert old[0]["content"] == "旧消息"
        assert old[0]["presentation"] == []
        assert old[0]["interaction"] is None
    finally:
        store.close()




def test_ecommerce_adapter_is_client_neutral_and_contains_no_html_or_css():
    source = agent_root(__file__) / "src" / "agent_modules" / "ecommerce" / "presentation" / "adapter.py"
    text = source.read_text(encoding="utf-8")
    assert "<div" not in text
    assert "class=" not in text
    assert "background:" not in text
    assert EcommerceObservationAdapter.adapter_id == "ecommerce.observations.v4"


def test_explicit_presentation_priority_is_deterministic():
    class Low:
        adapter_id = "low"
        priority = 1

        def blocks_from_trace(self, _trace):
            return [{"type": "summary", "role": "primary", "priority": 1, "content": "low"}]

    class High:
        adapter_id = "high"
        priority = 50

        def blocks_from_trace(self, _trace):
            return [{"type": "timeline", "role": "primary", "content": "high"}]

    blocks = PresentationRegistry([Low(), High()]).compose([])
    # Priority selection still picks the legacy high candidate first, but the
    # V3.3 release gate forbids it from reaching a formal response uncontracted.
    assert blocks[0]["type"] == "projection_contract_violation"
    assert "registered_presentation_contract" in blocks[0]["contract_violation"]["missing_required_semantics"]


def test_transaction_drawer_summaries_keep_one_active_form_and_queue_the_rest():
    from agent_core.transaction.interaction import pending_transaction_summaries_from_state
    from agent_core.ledger import artifact_entry, offer_entry

    scope = {"user_id": "u001", "thread_id": "thread-1", "tenant_id": "tenant-a"}
    mouse = artifact_entry(resource_type="order", resource_id="10003", label="无线鼠标（订单 10003）", facts={}, scope=scope, turn=1, source="test", handle="h_artifact:mouse")
    keyboard = artifact_entry(resource_type="order", resource_id="10002", label="机械键盘（订单 10002）", facts={}, scope=scope, turn=1, source="test", handle="h_artifact:keyboard")
    cancel = offer_entry(action_id="cancel_order", operation="cancel_order", target_handle=mouse["handle"], input_values={}, preview={"message": "请补充取消原因。"}, scope=scope, turn=1, label="取消订单", handle="h_offer:cancel")
    from agent_core.transaction import transition_draft
    cancel = transition_draft(cancel, "NEEDS_INPUT")
    cancel.update({"input_form_id": "form-1", "input_form_version": 1})
    refund = offer_entry(action_id="create_refund", operation="create_refund", target_handle=keyboard["handle"], input_values={}, preview={"message": "等待当前事项完成后继续。"}, scope=scope, turn=1, label="申请退款", handle="h_offer:refund")
    refund = transition_draft(refund, "READY")
    state = {
        "current_user_id": "u001", "current_thread_id": "thread-1", "current_tenant_id": "tenant-a",
        "active_draft_id": cancel["handle"], "action_queue": [{"offer_handle": refund["handle"]}],
        "artifact_ledger": [mouse, keyboard, cancel, refund],
    }

    summaries = pending_transaction_summaries_from_state(state)
    assert [(row["interaction_id"], row["active"], row["lifecycle"]) for row in summaries] == [
        ("h_offer:cancel", True, "collecting_input"),
        ("h_offer:refund", False, "queued"),
    ]


def test_plain_terminal_answer_cannot_claim_a_card_or_button_without_interaction():
    from agent_core.lifecycle.finalizer import _answer_from_terminal_tool

    state = {"tool_trace": [], "artifact_ledger": [], "current_user_id": "u001", "current_thread_id": "t", "current_tenant_id": "tenant"}
    answer, error, _ = _answer_from_terminal_tool(
        state,
        {"name": "respond_to_user", "args": {"answer": "系统会在下方显示卡片，请点击确认按钮。", "evidence_handles": []}},
    )
    assert answer is None
    assert error == "final_answer_claims_unbacked_interaction_ui"

    answer, error, _ = _answer_from_terminal_tool(
        state,
        {"name": "respond_to_user", "args": {"answer": "当前需要补充问题描述后才能继续办理。", "evidence_handles": []}},
    )
    assert error is None
    assert answer == "当前需要补充问题描述后才能继续办理。"
