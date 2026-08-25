from __future__ import annotations

import json
from pathlib import Path

from agent_core.lifecycle.protocol import DECLARE_TURN_GOALS_SCHEMA


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"


def _oracle(case_id: str) -> dict[str, list[str]]:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    case = next(row for row in payload["cases"] if row["id"] == case_id)
    rows = case["execution_contract"]["turn_contracts"][0]["goal_oracle"]
    return {row["oracle_id"]: list(row.get("depends_on") or []) for row in rows}


def test_input_binding_schema_distinguishes_result_consumption_from_shared_subject() -> None:
    goal_schema = DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]["goals"]["items"]
    assert "depends_on" not in goal_schema["properties"]
    binding_schema = goal_schema["properties"]["input_bindings"]["items"]
    description = binding_schema["description"]
    source_kinds = {
        row["properties"]["kind"]["const"]
        for row in binding_schema["properties"]["source"]["oneOf"]
    }
    assert source_kinds == {"current_goal_output", "current_text", "visible_result_ref"}
    assert "current_goal_output" in description
    assert "共享当前原文对象" in description
    assert "历史可见结果" in description
    assert "不产生当前轮边" in description
    for forbidden in ("list_orders", "get_order_logistics", "prepare_refund"):
        assert forbidden not in description


def test_shared_scope_ellipsis_and_explicit_result_reference_stay_distinct() -> None:
    assert _oracle("semantic_multi_orders_logistics") == {"g1": [], "g2": []}
    assert _oracle("semantic_query_then_refund_draft") == {"g1": [], "g2": []}
    assert _oracle("semantic_query_then_refund_consult") == {"g1": [], "g2": ["g1"]}
