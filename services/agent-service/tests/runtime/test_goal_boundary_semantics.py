from __future__ import annotations

import json
from pathlib import Path
import sys


AGENT_ROOT = Path(__file__).resolve().parents[2]
if str(AGENT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT / "src"))

from agent_core.lifecycle.protocol import DECLARE_TURN_GOALS_SCHEMA  # noqa: E402


CATALOG = AGENT_ROOT / "tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
CASE_ID = "semantic_cancel_and_refund_branch"


def _turn_contract() -> dict:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    case = next(row for row in payload["cases"] if row["id"] == CASE_ID)
    return case["execution_contract"]["turn_contracts"][0]


def test_cancel_and_refund_are_two_business_outcomes() -> None:
    contract = _turn_contract()
    oracle = contract["goal_oracle"]

    assert len(oracle) == 2
    assert [row["requested_effect"]["operation"] for row in oracle] == [
        "cancel",
        "assess_eligibility",
    ]
    assert [row["oracle_id"] for row in oracle] == ["g1", "g2"]
    assert contract["expected"]["goal_count"] == 2


def test_shared_order_lookup_is_execution_step_not_third_goal() -> None:
    contract = _turn_contract()
    execution_calls = contract["model_steps"][1]["tool_calls"]
    list_calls = [row for row in execution_calls if row["name"] == "list_orders"]
    calls_by_name = {
        row["name"]: row
        for row in execution_calls
        if row["name"] != "list_orders"
    }

    assert len(list_calls) == 2
    assert {tuple(row["args"]["goal_ids"]) for row in list_calls} == {("g1",), ("g2",)}
    assert all(len(row["args"]["goal_ids"]) == 1 for row in execution_calls)
    assert calls_by_name["prepare_cancel_order"]["args"]["goal_ids"] == ["g1"]
    assert calls_by_name["evaluate_refund_eligibility"]["args"]["goal_ids"] == ["g2"]
    assert all(
        row["requested_effect"]["operation"] != "list"
        for row in contract["goal_oracle"]
    )


def test_goal_protocol_defines_business_result_boundary() -> None:
    description = DECLARE_TURN_GOALS_SCHEMA["function"]["description"]

    assert "内部检索、筛选或目标解析" in description
    assert "不得凭空拆成额外 Goal" in description
    assert "多个独立结果即使共享同一检索步骤也必须分别声明" in description
