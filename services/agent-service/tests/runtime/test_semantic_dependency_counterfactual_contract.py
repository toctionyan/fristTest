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


def test_depends_on_schema_requires_result_counterfactual_not_discourse_order() -> None:
    goal_schema = DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]["goals"]["items"]
    description = goal_schema["properties"]["depends_on"]["description"]
    assert "结果反事实" in description
    assert "用户可见结果尚未产生" in description
    assert "仍能独立确定自己要得到的业务结果并独立判断完成" in description
    assert "再/然后/另外" in description
    assert "evidence_span 仍保持分支局部" in description
    assert "对象/成员名称属于目标身份而不是人口筛选" in description
    for forbidden in ("list_orders", "get_order_logistics", "prepare_refund"):
        assert forbidden not in description


def test_shared_scope_ellipsis_and_explicit_result_reference_stay_distinct() -> None:
    assert _oracle("semantic_multi_orders_logistics") == {"g1": [], "g2": []}
    assert _oracle("semantic_query_then_refund_draft") == {"g1": [], "g2": []}
    assert _oracle("semantic_query_then_refund_consult") == {"g1": [], "g2": ["g1"]}
