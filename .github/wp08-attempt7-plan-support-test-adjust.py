#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path("candidate").resolve()
TEST = ROOT / "skill-system/tests/test_wp08_attempt7_root_fixes.py"


def replace_region(start_marker: str, end_marker: str, replacement: str) -> None:
    text = TEST.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    TEST.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


replace_region(
    "def test_independent_shared_scope_goals_have_same_batch_completion_paths() -> None:\n",
    "def test_true_result_reference_oracle_is_still_dependent() -> None:\n",
    r'''def test_independent_shared_scope_reads_batch_but_write_uses_support_continuation() -> None:
    orders = _turn("semantic_multi_orders_logistics")
    assert _step_index(orders, "list_orders") == _step_index(orders, "get_order_logistics")
    assert _call(orders, "get_order_logistics")["args"]["target"] == {"mode": "all_orders"}

    detail = _turn("semantic_order_detail_and_invoice")
    assert _step_index(detail, "get_order_details") == _step_index(detail, "list_invoices")
    assert _call(detail, "get_order_details")["args"]["target"] == {
        "mode": "artifact",
        "left_handle": "artifact:fixture:order:10002",
    }
    assert _call(detail, "list_invoices")["args"]["target"] == {
        "mode": "artifact",
        "left_handle": "artifact:fixture:order:10002",
    }

    refund = _turn("semantic_query_then_refund_draft")
    assert _step_index(refund, "list_orders") < _step_index(refund, "prepare_refund")
    assert _call(refund, "list_orders")["args"]["goal_ids"] == ["g1"]
    assert _call(refund, "prepare_refund")["args"]["goal_ids"] == ["g2"]
    assert _call(refund, "prepare_refund")["args"]["target"] == {
        "mode": "artifact",
        "left_handle": "artifact:fixture:order:10003",
    }


''',
)

replace_region(
    "def test_multi_target_write_is_one_user_goal_and_directly_proves_cardinality_boundary() -> None:\n",
    "def test_shared_scope_ellipsis_rule_is_consistent_across_semantic_surfaces() -> None:\n",
    r'''def test_multi_target_write_is_one_user_goal_with_support_read_not_fake_goal() -> None:
    turn = _turn("semantic_multi_target_cancel_boundary")
    assert len(turn["goal_oracle"]) == 1
    goal = turn["goal_oracle"][0]
    assert goal["oracle_id"] == "g1"
    assert goal["evidence_span"] == "把这些订单都取消"
    assert goal["requested_effect"]["operation"] == "cancel"
    assert goal["depends_on"] == []
    scripted = _scripted_goals(turn)
    assert len(scripted) == 1
    assert scripted[0]["goal_id"] == "g1"
    assert scripted[0]["depends_on"] == []

    support = _call(turn, "list_orders")
    cancel = _call(turn, "prepare_cancel_order")
    assert _step_index(turn, "list_orders") < _step_index(turn, "prepare_cancel_order")
    assert support["args"]["goal_ids"] == ["g1"]
    assert cancel["args"]["goal_ids"] == ["g1"]
    assert cancel["args"]["target"] == {
        "mode": "collection",
        "left_handle": "$last_tool.data.result_handle",
    }
    assert "list_orders" in turn["allowed_tools"]
    assert "list_orders" in turn["required_tools"]
    assert "list_orders" in _case("semantic_multi_target_cancel_boundary")["execution_contract"]["preproduction_allowed_tools"]
    assert turn["expected"]["goal_count"] == 1


def test_execution_support_continuation_is_exact_non_completion_and_write_scoped() -> None:
    workflow = (AGENT_SRC / "agent_core/lifecycle/workflow_runtime.py").read_text(encoding="utf-8")
    policy = (AGENT_SRC / "agent_core/lifecycle/pretool_execution_policy.py").read_text(encoding="utf-8")
    list_orders = (AGENT_SRC / "agent_modules/ecommerce/capabilities/list_orders.py").read_text(encoding="utf-8")

    assert '"support_continuation_goal_ids": sorted(support_continuation_by_goal)' in workflow
    assert '"code": "PLAN_REQUIRED_GOAL_DEFERRED_BY_EXACT_SUPPORT"' in workflow
    assert '"goal_remains_incomplete": True' in workflow
    assert '"target_authority_granted": False' in workflow
    assert 'str(surface_goal.get("status") or "") == "exact_supported"' in workflow
    assert 'identity in support_effect_identities' in workflow

    assert 'str(contract.execution_kind or "") == "action_draft"' in policy
    assert '"support_frontier_tools": support_frontier' in policy
    assert '"support_frontier_is_completion": False' in policy
    assert 'tool_name not in completed_tools' in policy

    assert "'refund.create:order'" in list_orders
    assert "'order.cancel:order'" in list_orders


''',
)

print(TEST)
