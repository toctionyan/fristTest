from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected one replacement, found {count}: {old[:100]!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    protocol = ROOT / "services/agent-service/src/agent_core/lifecycle/protocol.py"
    replace_once(
        protocol,
        "MAX_WORK_ITEMS = 12\n\n\n_GOAL_LIFECYCLE_ENUM",
        '''MAX_WORK_ITEMS = 12

GOAL_DEPENDENCY_DECLARATION_RULE = (
    "depends_on 只在同一轮中一个业务结果必须使用另一个 Goal 的业务结果或以其为语义先决条件时声明。"
    "‘再/然后/并且’等话语顺序、共享同一对象、共享内部检索或筛选步骤都不构成依赖。"
    "支持分支与不支持分支默认相互独立；只有后一个结果明确引用前一个结果时才建立依赖。"
)


_GOAL_LIFECYCLE_ENUM''',
    )
    replace_once(
        protocol,
        '"depends_on": {"type": "array", "items": {"type": "string"}},',
        '''"depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": GOAL_DEPENDENCY_DECLARATION_RULE,
                            },''',
    )

    dialogue = ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
    replace_once(
        dialogue,
        "from agent_core.lifecycle.protocol import TERMINAL_TOOL_NAMES, agent_loop_schemas, planning_schemas",
        '''from agent_core.lifecycle.protocol import (
    GOAL_DEPENDENCY_DECLARATION_RULE,
    TERMINAL_TOOL_NAMES,
    agent_loop_schemas,
    planning_schemas,
)''',
    )
    replace_once(
        dialogue,
        "每个 Goal 必须给出开放 requested_effect(domain/operation/object_type/raw_description)、字面 evidence_span、对象/输入候选、条件和依赖。系统没有对应能力时仍保留原 Goal，",
        '每个 Goal 必须给出开放 requested_effect(domain/operation/object_type/raw_description)、字面 evidence_span、对象/输入候选、条件和依赖。"\n        + GOAL_DEPENDENCY_DECLARATION_RULE\n        + "系统没有对应能力时仍保留原 Goal，',
    )

    planning = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    replace_once(
        planning,
        "from agent_core.lifecycle.protocol import TERMINAL_TOOL_NAMES, classify_tool",
        '''from agent_core.lifecycle.protocol import (
    GOAL_DEPENDENCY_DECLARATION_RULE,
    TERMINAL_TOOL_NAMES,
    classify_tool,
)''',
    )
    replace_once(
        planning,
        '"a later outcome that relies on an earlier selection or query must declare depends_on that earlier goal",',
        "GOAL_DEPENDENCY_DECLARATION_RULE,",
    )

    smoke = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
    replace_once(
        smoke,
        "from agent_core.lifecycle.protocol import planning_schemas  # noqa: E402",
        '''from agent_core.lifecycle.protocol import (  # noqa: E402
    GOAL_DEPENDENCY_DECLARATION_RULE,
    planning_schemas,
)''',
    )
    replace_once(
        smoke,
        "\ndef main() -> int:\n",
        '''
def _semantic_system_instruction() -> str:
    return (
        "只执行目标声明：调用 declare_turn_goals，完整保留用户明确要求的每一个独立业务结果、条件和依赖。"
        "内部查找、筛选和目标解析只是执行步骤，不单独声明为 Goal，除非用户明确要求返回该查询结果。"
        "多个独立结果即使共享同一查找步骤也必须分别声明；不能吞掉不支持分支，也不能用相似能力代替。"
        + GOAL_DEPENDENCY_DECLARATION_RULE
        + "evidence_span 应覆盖该结果的动作或问题及关键对象条件，并且必须来自用户原话。"
    )


def main() -> int:
''',
    )
    replace_once(
        smoke,
        '''        system = SystemMessage(content=(
            "只执行目标声明：调用 declare_turn_goals，完整保留用户明确要求的每一个独立业务结果、条件和依赖。"
            "内部查找、筛选和目标解析只是执行步骤，不单独声明为 Goal，除非用户明确要求返回该查询结果。"
            "多个独立结果即使共享同一查找步骤也必须分别声明；不能吞掉不支持分支，也不能用相似能力代替。"
            "evidence_span 应覆盖该结果的动作或问题及关键对象条件，并且必须来自用户原话。"
        ))''',
        "        system = SystemMessage(content=_semantic_system_instruction())",
    )

    test_path = ROOT / "services/agent-service/tests/runtime/test_goal_dependency_declaration_semantics.py"
    if test_path.exists():
        raise SystemExit(f"{test_path}: test already exists")
    test_path.write_text(
        '''from __future__ import annotations

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
''',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
