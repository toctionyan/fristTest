from __future__ import annotations

from tests.support.paths import agent_root

from pathlib import Path


def _root() -> Path:
    return agent_root(__file__)


def _logistics_trace() -> list[dict]:
    return [
        {
            "name": "list_orders",
            "result": {"ok": True, "data": { "orders": [
                {"order_id": "10001", "product_name": "蓝牙耳机", "amount": 199, "status": "已发货"},
                {"order_id": "10003", "product_name": "无线鼠标", "amount": 99, "status": "待发货"},
            ]}},
        },
        {
            "name": "get_order_logistics",
            "result": {"ok": True, "data": { "items": [
                {"order": {"order_id": "10001", "product_name": "蓝牙耳机", "status": "已发货"}, "logistics": {"status": "运输中", "latest": "Phoenix 分拨中心"}},
                {"order": {"order_id": "10003", "product_name": "无线鼠标", "status": "待发货"}, "logistics": {"status": "待发货", "latest": "商家备货"}},
            ]}},
        },
    ]


def test_presentation_has_one_primary_and_one_distinct_secondary_summary():
    from agent_core.presentation.registry import PresentationRegistry, build_response_blocks
    from agent_modules.ecommerce.presentation.adapter import EcommerceObservationAdapter

    blocks = build_response_blocks({"tool_trace": _logistics_trace()}, registry=PresentationRegistry([EcommerceObservationAdapter()]))
    assert [row["type"] for row in blocks] == ["logistics_overview"]
    assert [row.get("role") for row in blocks] == ["primary"]
    assert all(row["type"] != "order_list" for row in blocks)


def test_presentation_adapter_failure_is_observable_and_does_not_block_healthy_adapter(caplog):
    from agent_core.presentation.registry import PresentationRegistry

    class Broken:
        adapter_id = "broken"
        priority = 100

        def blocks_from_trace(self, _trace):
            raise RuntimeError("adapter boom")

    class Healthy:
        adapter_id = "healthy"
        priority = 1

        def blocks_from_trace(self, _trace):
            return [{"type": "summary", "role": "primary", "content": "ok"}]

    with caplog.at_level("ERROR"):
        blocks = PresentationRegistry([Broken(), Healthy()]).compose([])

    assert blocks[0]["type"] == "projection_contract_violation"
    assert "registered_presentation_contract" in blocks[0]["contract_violation"]["missing_required_semantics"]
    assert any("presentation_adapter_failed" in record.message for record in caplog.records)


def test_customer_action_catalog_exposes_only_complete_registered_transaction_actions():
    """Module composition derives customer actions from the installed module registry.

    The former global ``CUSTOMER_ACTION_SPECS`` list was a second authority and
    has intentionally been removed.  Core metadata remains resource-generic;
    ecommerce presentation performs its order-id UI projection at the module
    boundary.
    """
    from agent_core.transaction.authority import registered_action_policy_ids
    from agent_core.presentation.actions import customer_actions_for_business_codes

    actions = customer_actions_for_business_codes(
        ["CANCEL_ORDER", "APPLY_REFUND", "APPLY_AFTER_SALES", "APPLY_INVOICE", "CHANGE_ADDRESS", "URGE_DELIVERY"],
        resource_type="order",
        resource_id="10003",
    )
    expected = [
        ("cancel_order", "取消订单", "我要取消订单 10003"),
        ("create_after_sales_request", "申请售后", "我要申请售后 10003"),
        ("create_refund", "申请退款", "我要申请退款 10003"),
        ("create_invoice", "申请发票", "我要申请发票 10003"),
    ]
    assert [(row["action_id"], row["label"], row["intent"]) for row in actions] == expected
    for action in actions:
        assert action["mode"] == "agent_transaction"
        assert action["capability_key"] == "business_application"
        assert action["target"] == {"resource_type": "order", "resource_id": "10003"}
        assert action["input_hints"] == {}
        assert isinstance(action["input_schema"], list)
        assert action["target_type"] == "order"
        assert action["action_id"] in registered_action_policy_ids()

    exposed_codes = {row["business_code"] for row in actions}
    assert "CHANGE_ADDRESS" not in exposed_codes
    assert "URGE_DELIVERY" not in exposed_codes
