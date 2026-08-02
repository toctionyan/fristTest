#!/usr/bin/env python3
"""One-time canonical repair for the cancel/refund semantic goal boundary.

The user asks for two business outcomes: cancel pending orders and assess
refund eligibility for delivered orders. Listing/filtering orders is a shared
execution step, not a third user Goal. This script rewrites only the exact
legacy case and fails closed if the expected source shape has drifted.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
CASE_ID = "semantic_cancel_and_refund_branch"


def _case(payload: dict) -> dict:
    matches = [row for row in payload.get("cases", []) if row.get("id") == CASE_ID]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {CASE_ID!r} case, found {len(matches)}")
    return matches[0]


def main() -> int:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    case = _case(payload)
    contract = case["execution_contract"]["turn_contracts"][0]
    user_text = "把待发货的订单取消，已签收的看看能不能退款"
    if contract.get("user_text") != user_text:
        raise SystemExit("cancel/refund user text drifted; refusing one-time repair")

    old_oracle = contract.get("goal_oracle")
    if not isinstance(old_oracle, list) or len(old_oracle) != 3:
        raise SystemExit("legacy cancel/refund oracle is not the expected three-goal shape")
    if [row.get("oracle_id") for row in old_oracle] != ["g1", "g2", "g3"]:
        raise SystemExit("legacy cancel/refund oracle IDs drifted")
    if [row.get("requested_effect", {}).get("operation") for row in old_oracle] != ["list", "cancel", "assess_eligibility"]:
        raise SystemExit("legacy cancel/refund oracle operations drifted")

    contract["goal_oracle"] = [
        {
            "oracle_id": "g1",
            "goal_type": "action",
            "evidence_span": "把待发货的订单取消",
            "required": True,
            "depends_on": [],
            "required_tools": ["prepare_cancel_order"],
            "requested_effect": {
                "domain": "order",
                "operation": "cancel",
                "object_type": "order",
            },
        },
        {
            "oracle_id": "g2",
            "goal_type": "consult",
            "evidence_span": "已签收的看看能不能退款",
            "required": True,
            "depends_on": [],
            "required_tools": ["evaluate_refund_eligibility"],
            "requested_effect": {
                "domain": "refund",
                "operation": "assess_eligibility",
                "object_type": "order",
            },
        },
    ]

    model_steps = contract.get("model_steps")
    if not isinstance(model_steps, list) or len(model_steps) < 2:
        raise SystemExit("legacy cancel/refund model steps drifted")
    declaration_calls = model_steps[0].get("tool_calls") or []
    if len(declaration_calls) != 1 or declaration_calls[0].get("name") != "declare_turn_goals":
        raise SystemExit("legacy cancel/refund declaration step drifted")
    declaration_args = declaration_calls[0]["args"]
    declaration_args["goals"] = [
        {
            "goal_id": "g1",
            "description": "取消待发货订单",
            "evidence_span": "把待发货的订单取消",
            "goal_type": "action",
            "expected_result_cardinality": "collection",
            "required": True,
            "depends_on": [],
            "requested_effect": {
                "domain": "order",
                "operation": "cancel",
                "object_type": "order",
                "raw_description": "取消待发货订单",
            },
            "condition": {"order_status": "pending_shipment"},
        },
        {
            "goal_id": "g2",
            "description": "核验已签收订单的退款资格",
            "evidence_span": "已签收的看看能不能退款",
            "goal_type": "consult",
            "expected_result_cardinality": "collection",
            "required": True,
            "depends_on": [],
            "requested_effect": {
                "domain": "refund",
                "operation": "assess_eligibility",
                "object_type": "order",
                "raw_description": "核验已签收订单的退款资格",
            },
            "condition": {"order_status": "delivered"},
        },
    ]

    execution_calls = model_steps[1].get("tool_calls") or []
    by_name = {row.get("name"): row for row in execution_calls}
    if set(by_name) != {"list_orders", "prepare_cancel_order", "evaluate_refund_eligibility"}:
        raise SystemExit("legacy cancel/refund execution tool set drifted")
    by_name["list_orders"]["args"]["goal_ids"] = ["g1", "g2"]
    by_name["list_orders"]["args"]["reference_span"] = user_text
    by_name["prepare_cancel_order"]["args"]["goal_ids"] = ["g1"]
    by_name["prepare_cancel_order"]["args"]["reference_span"] = "待发货的订单"
    by_name["evaluate_refund_eligibility"]["args"]["goal_ids"] = ["g2"]

    expected = contract.get("expected")
    if not isinstance(expected, dict) or expected.get("goal_count") != 3:
        raise SystemExit("legacy cancel/refund expected goal count drifted")
    expected["goal_count"] = 2

    CATALOG.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
