#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:180]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


planning = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
replace_once(
    planning,
    '            "and/then/next/also/再/然后/另外 or merely sharing the same business object/topic does not by itself create depends_on; independently acceptable sibling outcomes must keep depends_on empty",\n',
    '            "and/then/next/also/再/然后/另外 or merely sharing the same business object/topic does not by itself create depends_on; independently acceptable sibling outcomes must keep depends_on empty",\n            "when a later outcome omits its target but an earlier phrase in the same current user turn already names the reusable business object or scope, inherit that stated scope as ellipsis; that shared scope is not a dependency on the earlier Goal result by itself",\n',
)

runtime_prompt = ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
replace_once(
    runtime_prompt,
    "depends_on 只表示真实结果依赖：只有后一个 Goal 的目标、输入、条件或完成含义必须使用前一个 Goal 的结果才依赖；并列、再/然后/另外、共享业务对象或共享主题只是话语顺序/共同范围，不得据此制造依赖。若后一个 Goal 用它/这个/其中某项等指向本轮前一个 Goal 尚未产生的结果，或条件显式依赖前一个结果，则应声明 depends_on。",
    "depends_on 只表示真实结果依赖：只有后一个 Goal 的目标、输入、条件或完成含义必须使用前一个 Goal 的结果才依赖；并列、再/然后/另外、共享业务对象或共享主题只是话语顺序/共同范围，不得据此制造依赖。同一用户原话中前文已明确业务对象或范围、后文只是省略重复对象时，应继承这个已明示范围作为省略语义，不得因此依赖前一个 Goal 的执行结果。若后一个 Goal 用它/这个/其中某项等指向本轮前一个 Goal 尚未产生的结果，或条件显式依赖前一个结果，则应声明 depends_on。",
)

protocol = ROOT / "services/agent-service/src/agent_core/lifecycle/protocol.py"
replace_once(
    protocol,
    "同一当前轮内只有真实结果依赖才填写 depends_on：后一个 Goal 的目标、输入、条件或可完成含义必须使用前一个 Goal 的结果时才依赖；并列、再/然后/另外、共享对象或共享主题本身不构成依赖。不得为尚未执行的当前轮 Goal 的未来结果创建 reference_expression。",
    "同一当前轮内只有真实结果依赖才填写 depends_on：后一个 Goal 的目标、输入、条件或可完成含义必须使用前一个 Goal 的结果时才依赖；并列、再/然后/另外、共享对象或共享主题本身不构成依赖。同一原话前文已明确对象或范围、后文只省略重复对象时继承该已明示范围，不依赖前一个 Goal 的执行结果。不得为尚未执行的当前轮 Goal 的未来结果创建 reference_expression。",
)
replace_once(
    protocol,
    '                                    "并列、再/然后/另外等话语顺序、共享同一业务对象或同一主题本身都不是依赖；这些情况必须保持独立。"\n',
    '                                    "并列、再/然后/另外等话语顺序、共享同一业务对象或同一主题本身都不是依赖；这些情况必须保持独立。"\n                                    "同一原话前文已明确业务对象或范围而后文只省略重复对象时，应继承该明示范围；这不是对前一个 Goal 执行结果的依赖。"\n',
)

smoke = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
old_eval = r'''def _production_goal_declaration_evaluation(
    *, user_text: str, goals: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Evaluate a declaration through the exact production Runtime contract."""
    return validate_goal_declaration(
        state={"current_user_input": user_text},
        args={"goals": goals},
        capability_registry=get_runtime_registry().capabilities,
    )
'''
new_eval = r'''def _production_goal_declaration_evaluation(
    *,
    user_text: str,
    goals: list[dict[str, Any]],
    inventory_authority: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Evaluate through production Runtime, preserving turn-scoped blind authority."""
    state: dict[str, Any] = {"current_user_input": user_text}
    if isinstance(inventory_authority, dict):
        state["current_turn_plan"] = {
            "goal_granularity_inventory_authority": dict(inventory_authority),
        }
    return validate_goal_declaration(
        state=state,
        args={"goals": goals},
        capability_registry=get_runtime_registry().capabilities,
    )
'''
replace_once(smoke, old_eval, new_eval)
old_validate = r'''def _validate_with_production_goal_contract(
    *, case_id: str, user_text: str, goals: list[dict[str, Any]]
) -> dict[str, Any]:
    result, declared = _production_goal_declaration_evaluation(
        user_text=user_text,
        goals=goals,
    )
'''
new_validate = r'''def _validate_with_production_goal_contract(
    *,
    case_id: str,
    user_text: str,
    goals: list[dict[str, Any]],
    inventory_authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result, declared = _production_goal_declaration_evaluation(
        user_text=user_text,
        goals=goals,
        inventory_authority=inventory_authority,
    )
'''
replace_once(smoke, old_validate, new_validate)
replace_once(
    smoke,
    '    last_result: dict[str, Any] | None = None\n    for attempt in range(1, 3):\n',
    '    last_result: dict[str, Any] | None = None\n    inventory_authority: dict[str, Any] | None = None\n    for attempt in range(1, 3):\n',
)
replace_once(
    smoke,
    '''            declared = _validate_with_production_goal_contract(
                case_id=case_id,
                user_text=user_text,
                goals=goals,
            )
''',
    '''            declared = _validate_with_production_goal_contract(
                case_id=case_id,
                user_text=user_text,
                goals=goals,
                inventory_authority=inventory_authority,
            )
''',
)
replace_once(
    smoke,
    '''            result = exc.result
            last_result = result
''',
    '''            result = exc.result
            last_result = result
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            granularity = data.get("granularity_proof") if isinstance(data.get("granularity_proof"), dict) else {}
            details = granularity.get("details") if isinstance(granularity.get("details"), dict) else {}
            candidate_authority = details.get("inventory_authority")
            if isinstance(candidate_authority, dict):
                inventory_authority = dict(candidate_authority)
''',
)
replace_once(
    smoke,
    '''            "outcome_spans": [str(value) for value in list(details.get("outcome_spans") or []) if str(value)][:8],
            "blind_self_audit_attempted": bool(details.get("blind_self_audit_attempted")),
''',
    '''            "outcome_spans": [str(value) for value in list(details.get("outcome_spans") or []) if str(value)][:8],
            "dependency_edges": [
                {
                    "dependent_span": str(row.get("dependent_span") or ""),
                    "requires_result_of_span": str(row.get("requires_result_of_span") or ""),
                }
                for row in list(details.get("dependency_edges") or [])
                if isinstance(row, dict)
            ][:8],
            "dependency_graph_match": details.get("dependency_graph_match"),
            "inventory_authority_reused": bool(details.get("inventory_authority_reused")),
            "blind_self_audit_attempted": bool(details.get("blind_self_audit_attempted")),
''',
)
replace_once(
    smoke,
    '''        diagnostic["independent_verifier_feedback"] = {
            "authority": str(feedback.get("authority") or ""),
            "uncovered_outcome_spans": [str(value) for value in list(feedback.get("uncovered_outcome_spans") or []) if str(value)][:8],
        }
''',
    '''        diagnostic["independent_verifier_feedback"] = {
            "authority": str(feedback.get("authority") or ""),
            "uncovered_outcome_spans": [str(value) for value in list(feedback.get("uncovered_outcome_spans") or []) if str(value)][:8],
            "dependency_edges": [
                {
                    "dependent_span": str(row.get("dependent_span") or ""),
                    "requires_result_of_span": str(row.get("requires_result_of_span") or ""),
                }
                for row in list(feedback.get("dependency_edges") or [])
                if isinstance(row, dict)
            ][:8],
        }
''',
)

catalog_path = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
payload = json.loads(catalog_path.read_text(encoding="utf-8"))
by_id = {str(row.get("id") or ""): row for row in payload.get("cases") or []}


def turn_for(case_id: str) -> dict:
    case = by_id.get(case_id)
    if not isinstance(case, dict):
        raise SystemExit(f"missing semantic case: {case_id}")
    turns = (case.get("execution_contract") or {}).get("turn_contracts") or []
    if len(turns) != 1:
        raise SystemExit(f"expected one turn contract: {case_id}")
    return turns[0]


def scripted_goals(turn: dict) -> list[dict]:
    first = (turn.get("model_steps") or [])[0]
    call = next(row for row in first.get("tool_calls") or [] if row.get("name") == "declare_turn_goals")
    return (call.get("args") or {}).get("goals") or []


for case_id in (
    "semantic_multi_orders_logistics",
    "semantic_query_then_refund_draft",
    "semantic_order_detail_and_invoice",
):
    turn = turn_for(case_id)
    oracle = list(turn.get("goal_oracle") or [])
    if len(oracle) != 2:
        raise SystemExit(f"expected two oracle goals before dependency cleanup: {case_id}")
    oracle[1]["depends_on"] = []
    goals = scripted_goals(turn)
    if len(goals) != 2:
        raise SystemExit(f"expected two scripted goals before dependency cleanup: {case_id}")
    goals[1]["depends_on"] = []

multi = turn_for("semantic_multi_target_cancel_boundary")
old_oracle = list(multi.get("goal_oracle") or [])
if len(old_oracle) != 2 or str(old_oracle[0].get("oracle_id") or "") != "g0":
    raise SystemExit("multi-target oracle shape changed before repair")
action_oracle = dict(old_oracle[1])
action_oracle["evidence_span"] = "把这些订单都取消"
action_oracle["depends_on"] = []
multi["goal_oracle"] = [action_oracle]
old_scripted = scripted_goals(multi)
if len(old_scripted) != 2 or str(old_scripted[0].get("goal_id") or "") != "g0":
    raise SystemExit("multi-target scripted goal shape changed before repair")
action_goal = dict(old_scripted[1])
action_goal["description"] = "取消用户所指的订单集合"
action_goal["evidence_span"] = "把这些订单都取消"
action_goal["depends_on"] = []
first_call = next(
    row for row in (multi.get("model_steps") or [])[0].get("tool_calls") or []
    if row.get("name") == "declare_turn_goals"
)
first_call["args"]["goals"] = [action_goal]
for step in multi.get("model_steps") or []:
    for call in step.get("tool_calls") or []:
        if call.get("name") == "list_orders":
            call.setdefault("args", {})["goal_ids"] = ["g1"]
        elif call.get("name") == "respond_to_user":
            call.setdefault("args", {})["goal_ids"] = ["g1"]
multi.setdefault("expected", {})["goal_count"] = 1

catalog_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print("attempt7 semantic contract, preprod parity and oracle repair applied")
