from __future__ import annotations

from tests.support.paths import agent_root

from pathlib import Path
from typing import Any

import pytest

from agent_core.composition import get_runtime_registry
from agent_core.operations.registry import OperationPluginRegistry
from agent_core.business import ActorContext
from agent_core.security.thread_security import secure_checkpoint_thread_id


def test_customer_visible_actions_are_owned_by_plugin_registry():
    from agent_core.transaction.authority import registered_action_policy_ids
    from agent_core.presentation.actions import registered_action_ids
    from agent_core.lifecycle.nodes import COMMITTABLE_TRANSACTION_ACTION_IDS
    from agent_core.composition import get_runtime_registry

    registry_ids = get_runtime_registry().operations.action_ids()
    assert registry_ids == {
        "cancel_order",
        "create_after_sales_request",
        "create_refund",
        "create_invoice",
    }
    # The installed operation registry is the single authority; a global
    # CUSTOMER_ACTION_SPECS snapshot would become a second, stale authority.
    assert registered_action_ids() == registry_ids
    assert registered_action_policy_ids() == registry_ids
    assert set(get_runtime_registry().preparable_action_ids()) == registry_ids
    assert set(COMMITTABLE_TRANSACTION_ACTION_IDS) == registry_ids


def test_registry_rejects_duplicate_action_or_business_codes():
    plugins = get_runtime_registry().operations.all()
    with pytest.raises(ValueError):
        OperationPluginRegistry([*plugins, plugins[0]])

    duplicate_code = type(
        "DuplicateCodePlugin",
        (),
        {
            "action_id": "another_cancel",
            "business_code": plugins[0].business_code,
            "business_operation": plugins[0].business_operation,
            "label": "duplicate",
            "risk_level": "low_mutation",
            "target_type": "order",
            "input_schema": [],
        },
    )()
    with pytest.raises(ValueError):
        OperationPluginRegistry([*plugins, duplicate_code])


def test_order_action_plugin_builds_payload_and_commits_through_adapter():
    plugin = get_runtime_registry().operations.require("create_invoice")
    actor = ActorContext(user_id="u001", role="customer", tenant_id="tenant-a")
    calls: list[dict[str, Any]] = []

    class FakeAdapter:
        def execute_command(self, actor_arg, *, command, idempotency_key):
            calls.append(
                {
                    "actor": actor_arg,
                    "command": command,
                    "idempotency_key": idempotency_key,
                }
            )
            return {"success": True, "data": {"invoice_id": "I-1"}}

    payload = plugin.build_commit_payload(
        actor=actor,
        target={"resource_type": "order", "resource_id": "10001"},
        input_values={"invoice_title": "北京示例科技有限公司"},
        preview={"snapshot": {"version": 7}},
    )
    result = plugin.commit(
        FakeAdapter(),
        actor,
        target={"resource_type": "order", "resource_id": "10001"},
        payload=payload,
        idempotency_key="idem-1",
    )

    assert result["success"] is True
    assert calls[0]["command"]["action_id"] == "create_invoice"
    assert calls[0]["command"]["input"]["expected_version"] == 7
    assert calls[0]["command"]["actor_scope"]["user_id"] == "u001"
    assert calls[0]["idempotency_key"] == "idem-1"


def test_action_plugin_projects_business_result_artifacts():
    refund = get_runtime_registry().operations.require("create_refund")
    refund_rows = refund.project_result_artifacts(
        target={"resource_type": "order", "resource_id": "10001"},
        result={"success": True, "data": {"refund_id": "R-1", "version": 2}},
        existing_target={"handle": "h_order:10001", "label": "蓝牙耳机（订单 10001）", "resource_id": "10001"},
    )
    assert refund_rows == [
        {
            "resource_type": "refund",
            "resource_id": "R-1",
            "label": "refund:R-1",
            "facts": {"refund_id": "R-1", "version": 2},
            "freshness_version": 2,
        }
    ]

    cancel = get_runtime_registry().operations.require("cancel_order")
    cancel_rows = cancel.project_result_artifacts(
        target={"resource_type": "order", "resource_id": "10001"},
        result={"success": True, "data": {"order_id": "10001", "status": "已取消", "version": 8}},
        existing_target={"handle": "h_order:10001", "label": "蓝牙耳机（订单 10001）", "resource_id": "10001"},
    )
    assert cancel_rows[0]["resource_type"] == "order"
    assert cancel_rows[0]["handle"] == "h_order:10001"
    assert cancel_rows[0]["freshness_version"] == 8


def test_checkpoint_thread_id_is_langgraph_postgres_safe_length():
    raw = secure_checkpoint_thread_id(
        "thread-" + ("x" * 500),
        "user-" + ("y" * 200),
        "tenant-" + ("z" * 200),
    )
    assert len(raw) <= 255
    assert "thread_hash=" in raw


def test_product_api_actions_are_registry_metadata():
    from app.api.product_api import list_actions

    payload = list_actions()
    rows = payload["actions"]
    assert {row["action_id"] for row in rows} == get_runtime_registry().operations.action_ids()
    assert all("input_schema" in row for row in rows)
    assert all(row["target_type"] == "order" for row in rows)


def test_no_new_customer_action_commit_dispatch_table():
    root = agent_root(__file__)
    dispatcher = (root / "src" / "agent_core" / "lifecycle" / "nodes.py").read_text(encoding="utf-8")
    service = (root / "app" / "services" / "agent_service.py").read_text(encoding="utf-8")
    legacy_safe_skills = root / "src" / "agent_core" / "tools" / "safe_skills.py"

    assert 'if action == "create_refund"' not in dispatcher
    assert 'if action == "create_after_sales_request"' not in dispatcher
    assert 'if action == "create_invoice"' not in dispatcher
    assert 'if action == "cancel_order"' not in dispatcher
    assert '"create_refund": ("refund", "refund_id")' not in dispatcher
    assert 'if str(offer.get("action_id") or "") == "cancel_order"' not in dispatcher
    assert "get_business_client().preview_operation" not in service
    assert not legacy_safe_skills.exists()
