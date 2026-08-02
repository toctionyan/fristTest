from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_core.lifecycle.protocol import (
    DECLARE_TURN_GOALS_SCHEMA,
    GOAL_DEPENDENCY_DECLARATION_RULE,
)
from scripts.verify_preprod_conversation_smoke import (
    _assert_effect_evidence_coverage,
    _semantic_system_instruction,
)


CATALOG = (
    Path(__file__).resolve().parents[1]
    / "context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
)
CASE_ID = "semantic_supported_plus_unsupported"


def _turn() -> dict:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    case = next(row for row in payload["cases"] if row["id"] == CASE_ID)
    return case["execution_contract"]["turn_contracts"][0]


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


@pytest.mark.parametrize("unsupported_depends_on", [[], ["model-logistics"]])
def test_effect_oracle_accepts_equivalent_dependency_representations(
    unsupported_depends_on: list[str],
) -> None:
    turn = _turn()
    _assert_effect_evidence_coverage(
        case_id=CASE_ID,
        oracle=turn["goal_oracle"],
        goals=_model_goals(unsupported_depends_on=unsupported_depends_on),
    )


def test_effect_oracle_accepts_composite_evidence_representation() -> None:
    turn = _turn()
    _assert_effect_evidence_coverage(
        case_id=CASE_ID,
        oracle=turn["goal_oracle"],
        goals=[{
            "goal_id": "model-composite",
            "evidence_span": turn["user_text"],
            "required": True,
            "depends_on": [],
        }],
    )


def test_effect_oracle_rejects_missing_requested_effect() -> None:
    turn = _turn()
    with pytest.raises(RuntimeError, match="required effect evidence not covered"):
        _assert_effect_evidence_coverage(
            case_id=CASE_ID,
            oracle=turn["goal_oracle"],
            goals=_model_goals(unsupported_depends_on=[])[:1],
        )


def test_dependency_boundary_is_not_a_lexical_classifier() -> None:
    depends_on = (
        DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]["goals"]
        ["items"]["properties"]["depends_on"]
    )
    assert depends_on["description"] == GOAL_DEPENDENCY_DECLARATION_RULE
    assert "程序只验证引用存在" in GOAL_DEPENDENCY_DECLARATION_RULE
    assert "局部 Plan" in GOAL_DEPENDENCY_DECLARATION_RULE
    assert "再/然后/并且" not in GOAL_DEPENDENCY_DECLARATION_RULE
    instruction = _semantic_system_instruction()
    assert GOAL_DEPENDENCY_DECLARATION_RULE in instruction


def test_protected_smoke_does_not_bind_unique_semantic_ast() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "scripts/verify_preprod_conversation_smoke.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "goal count mismatch",
        "goal dependency mismatch",
        "model emitted undeclared extra goals",
        "actual_dependencies != expected_dependencies",
    ):
        assert forbidden not in source
    assert "_assert_effect_evidence_coverage" in source
