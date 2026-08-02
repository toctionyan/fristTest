from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_core.lifecycle.protocol import (
    DECLARE_TURN_GOALS_SCHEMA,
    GOAL_DEPENDENCY_DECLARATION_RULE,
)
from scripts.verify_preprod_conversation_smoke import (
    _match_oracle,
    _semantic_system_instruction,
)


CATALOG = (
    Path(__file__).resolve().parents[1]
    / "context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
)
CASE_ID = "semantic_supported_plus_unsupported"


def _oracle() -> list[dict]:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    case = next(row for row in payload["cases"] if row["id"] == CASE_ID)
    return case["execution_contract"]["turn_contracts"][0]["goal_oracle"]


def _model_goals(*, unsupported_depends_on: list[str]) -> list[dict]:
    return [
        {
            "goal_id": "model-logistics",
            "evidence_span": "查一下鼠标物流",
            "required": True,
            "depends_on": [],
        },
        {
            "goal_id": "model-phone",
            "evidence_span": "快递员手机号",
            "required": True,
            "depends_on": unsupported_depends_on,
        },
    ]


def test_shared_target_and_discourse_order_do_not_create_goal_dependency() -> None:
    _match_oracle(
        case_id=CASE_ID,
        oracle=_oracle(),
        goals=_model_goals(unsupported_depends_on=[]),
    )


def test_false_dependency_between_supported_and_unsupported_goals_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="goal dependency mismatch"):
        _match_oracle(
            case_id=CASE_ID,
            oracle=_oracle(),
            goals=_model_goals(unsupported_depends_on=["model-logistics"]),
        )


def test_dependency_rule_is_bound_to_schema_and_live_certification_prompt() -> None:
    depends_on = (
        DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]["goals"]
        ["items"]["properties"]["depends_on"]
    )

    assert depends_on["description"] == GOAL_DEPENDENCY_DECLARATION_RULE
    instruction = _semantic_system_instruction()
    assert GOAL_DEPENDENCY_DECLARATION_RULE in instruction
    assert "共享同一对象" in instruction
    assert "支持分支与不支持分支默认相互独立" in instruction
