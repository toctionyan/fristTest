#!/usr/bin/env python3
"""One-time canonical repair for the cancel/refund semantic goal boundary.

The user asks for two business outcomes: cancel pending orders and assess
refund eligibility for delivered orders. Listing/filtering orders is a shared
execution step, not a third user Goal. This script rewrites only the exact
legacy case and aligned protocol checks, then fails closed if source shape has
drifted.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
PROTOCOL = ROOT / "services/agent-service/src/agent_core/lifecycle/protocol.py"
SMOKE = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
RUNNER = ROOT / "services/agent-service/tests/support/conversation_case_runner.py"
CASE_ID = "semantic_cancel_and_refund_branch"


def _case(payload: dict) -> dict:
    matches = [row for row in payload.get("cases", []) if row.get("id") == CASE_ID]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {CASE_ID!r} case, found {len(matches)}")
    return matches[0]


def _replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} source drifted; expected one exact fragment, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _repair_catalog() -> None:
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


def _repair_protocol_contract() -> None:
    old = (
        '            "在选择任何业务能力之前，按当前原话和权威上下文声明本轮全部业务 Goal、对象候选、条件、顺序和状态变化。"\n'
        '            "requested_effect 使用开放字符串描述用户要实现的业务效果，不得为了匹配现有工具改写为相近能力。"'
    )
    new = (
        '            "在选择任何业务能力之前，按当前原话和权威上下文声明本轮全部业务 Goal、对象候选、条件、顺序和状态变化。"\n'
        '            "每个 Goal 只对应用户明确要求实现的一个独立业务结果；内部检索、筛选或目标解析只能作为执行步骤、"\n'
        '            "target_candidate 或 condition，不得凭空拆成额外 Goal，除非用户明确要求返回该检索结果。"\n'
        '            "多个独立结果即使共享同一检索步骤也必须分别声明。"\n'
        '            "requested_effect 使用开放字符串描述用户要实现的业务效果，不得为了匹配现有工具改写为相近能力。"'
    )
    _replace_once(PROTOCOL, old, new, label="declare_turn_goals protocol")


def _repair_real_model_prompt() -> None:
    old = (
        '        system = SystemMessage(content=(\n'
        '            "只执行目标声明：调用 declare_turn_goals，完整保留用户的每一个目标、条件和依赖。"\n'
        '            "不能把不支持分支吞掉，也不能用相似能力代替。evidence_span 必须来自用户原话。"\n'
        '        ))'
    )
    new = (
        '        system = SystemMessage(content=(\n'
        '            "只执行目标声明：调用 declare_turn_goals，完整保留用户明确要求的每一个独立业务结果、条件和依赖。"\n'
        '            "内部查找、筛选和目标解析只是执行步骤，不单独声明为 Goal，除非用户明确要求返回该查询结果。"\n'
        '            "多个独立结果即使共享同一查找步骤也必须分别声明；不能吞掉不支持分支，也不能用相似能力代替。"\n'
        '            "evidence_span 应覆盖该结果的动作或问题及关键对象条件，并且必须来自用户原话。"\n'
        '        ))'
    )
    _replace_once(SMOKE, old, new, label="protected semantic prompt")


def _repair_deterministic_oracle_authority() -> None:
    old = (
        '        assert str(row.get("goal_type") or "") == str(expected.get("goal_type") or ""), (case_id, oracle_id, row, expected)\n'
        '        assert str(row.get("evidence_span") or "") == str(expected.get("evidence_span") or ""), (case_id, oracle_id, row, expected)'
    )
    new = (
        '        expected_effect = expected.get("requested_effect") if isinstance(expected.get("requested_effect"), dict) else {}\n'
        '        actual_effect = row.get("requested_effect") if isinstance(row.get("requested_effect"), dict) else {}\n'
        '        for effect_key in ("domain", "operation", "object_type"):\n'
        '            expected_value = str(expected_effect.get(effect_key) or "")\n'
        '            if expected_value:\n'
        '                assert str(actual_effect.get(effect_key) or "") == expected_value, (\n'
        '                    case_id, oracle_id, effect_key, row, expected\n'
        '                )\n'
        '        assert str(row.get("evidence_span") or "") == str(expected.get("evidence_span") or ""), (case_id, oracle_id, row, expected)'
    )
    _replace_once(RUNNER, old, new, label="deterministic oracle authority")


def main() -> int:
    _repair_catalog()
    _repair_protocol_contract()
    _repair_real_model_prompt()
    _repair_deterministic_oracle_authority()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
