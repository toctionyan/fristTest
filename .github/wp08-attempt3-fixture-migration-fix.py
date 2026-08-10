from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONV = ROOT / "services/agent-service/tests/context/strong_context_cases/conversation_runtime_contract_suite_v20_4.json"
SEM = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
GOAL_TEST = ROOT / "services/agent-service/tests/runtime/test_goal_coverage_runtime.py"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def declaration_goals(payload: dict, case_id: str, *, user_text: str | None = None) -> list[dict]:
    cases = {str(row.get("id") or ""): row for row in list(payload.get("cases") or []) if isinstance(row, dict)}
    case = cases.get(case_id)
    if case is None:
        raise SystemExit(f"fixture case not found: {case_id}")
    contracts = list(((case.get("execution_contract") or {}).get("turn_contracts") or []))
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        if user_text is not None and str(contract.get("user_text") or "") != user_text:
            continue
        for step in list(contract.get("model_steps") or []):
            if not isinstance(step, dict):
                continue
            for call in list(step.get("tool_calls") or []):
                if not isinstance(call, dict) or str(call.get("name") or "") != "declare_turn_goals":
                    continue
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                goals = [row for row in list(args.get("goals") or []) if isinstance(row, dict)]
                if goals:
                    return goals
    raise SystemExit(f"declare_turn_goals fixture not found: {case_id}:{user_text or '*'}")


def by_id(goals: list[dict], goal_id: str) -> dict:
    matches = [row for row in goals if str(row.get("goal_id") or "") == goal_id]
    if len(matches) != 1:
        raise SystemExit(f"expected one goal {goal_id}, got {len(matches)}")
    return matches[0]


conv = load(CONV)

# Independent sibling outcomes sharing an explicitly stated same-turn scope.
goals = declaration_goals(conv, "multi_query_orders_and_logistics")
g1, g2 = by_id(goals, "g1"), by_id(goals, "g2")
g1["description"] = "查询我的订单"
g1["evidence_span"] = "查一下我的订单"
g1["depends_on"] = []
g1["dependency_bindings"] = []
g1["target_binding"] = {"source": "local_literal", "evidence_span": "我的订单"}
g2["description"] = "查询这些订单的物流"
g2["evidence_span"] = "查下物流到哪了"
g2["depends_on"] = []
g2["dependency_bindings"] = []
g2["target_binding"] = {
    "source": "same_turn_literal_scope",
    "source_goal_id": "g1",
    "evidence_span": "我的订单",
}

# These two preserved target-selector fixtures intentionally keep their legacy
# two-Goal shape. Because the second Goal truly consumes the first Goal's
# selected result, make that dependency explicit rather than grandfathering an
# ungrounded depends_on edge.
for case_id, user_text, dependent_id, source_id, evidence in (
    ("correction_latest_to_most_expensive", "最近那个能退吗", "g1", "g0", "最近那个"),
    ("visible_superlative_cheapest", "最便宜的那个能退吗？", "g1", "g0", "最便宜的那个"),
):
    goals = declaration_goals(conv, case_id, user_text=user_text)
    source = by_id(goals, source_id)
    dependent = by_id(goals, dependent_id)
    source["dependency_bindings"] = []
    dependent["target_binding"] = {
        "source": "current_turn_goal_output",
        "source_goal_id": source_id,
        "evidence_span": evidence,
    }
    dependent["dependency_bindings"] = [{
        "source_goal_id": source_id,
        "relation": "target",
        "evidence_span": evidence,
    }]

save(CONV, conv)

sem = load(SEM)
# True current-turn result reference: `它` genuinely denotes g1's future result.
goals = declaration_goals(sem, "semantic_query_then_refund_consult")
g1, g2 = by_id(goals, "g1"), by_id(goals, "g2")
g1["dependency_bindings"] = []
g2["target_binding"] = {
    "source": "current_turn_goal_output",
    "source_goal_id": "g1",
    "evidence_span": "它",
}
g2["dependency_bindings"] = [{
    "source_goal_id": "g1",
    "relation": "target",
    "evidence_span": "它",
}]

# Core Attempt-3 negative case: the mouse descriptor is already literal in g1,
# so g2 remains independent and explicitly records same-turn scope reuse.
goals = declaration_goals(sem, "semantic_query_then_refund_draft")
g1, g2 = by_id(goals, "g1"), by_id(goals, "g2")
g1["dependency_bindings"] = []
g1["target_binding"] = {"source": "local_literal", "evidence_span": "鼠标订单"}
g2["depends_on"] = []
g2["dependency_bindings"] = []
g2["target_binding"] = {
    "source": "same_turn_literal_scope",
    "source_goal_id": "g1",
    "evidence_span": "鼠标订单",
}

save(SEM, sem)

source = GOAL_TEST.read_text(encoding="utf-8")
old = '''                    "goal_type": "query",
                    "required": True,
                    "depends_on": ["g1"],
                    "expected_tools": ["get_order_logistics"],
'''
new = '''                    "goal_type": "query",
                    "required": True,
                    "depends_on": [],
                    "target_binding": {
                        "source": "same_turn_literal_scope",
                        "source_goal_id": "g1",
                        "evidence_span": "订单",
                    },
                    "dependency_bindings": [],
                    "expected_tools": ["get_order_logistics"],
'''
if source.count(old) != 1:
    raise SystemExit(f"goal coverage fixture anchor expected once, got {source.count(old)}")
GOAL_TEST.write_text(source.replace(old, new, 1), encoding="utf-8")

print("Attempt-3 deterministic fixture migration applied")
